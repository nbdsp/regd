#!/usr/bin/env python
'''********************************************************************
*	Module:			regd
*	
*	File name:		regd.py
*
*	Created:		2015-Apr-05 06:20:25 PM
*
*	Abstract:		Registry daemon. 
*
*	Copyright:		Albert Berger, 2015.
*
*********************************************************************'''

__lastedited__ = "2015-06-16 01:53:48"

VERSION = ( 0, 4 )
__version__ = '.'.join( map( str, VERSION[0:2] ) )
__description__ = 'Registry daemon and secure data cache'
__author__ = 'Albert Berger'
__author_email__ = 'nbdspcl@gmail.com'
__homepage__ = 'https://github.com/nbdsp/regd'
__license__ = 'GPL'

import sys, os, socket, signal, subprocess, logging, argparse, time, regex, pwd
from configparser import ConfigParser

APPNAME = "regd"
THISFILE = os.path.basename( __file__ )
EODMARKER = "%^&"
CMDMARKER = "&&&"
ITEMMARKER = "@#$"
GLSEC = "global"

server_address = None
host = None
port = None
sockname = 'regd.sock'
homedir = None

tokens = ConfigParser()
sectokens = ConfigParser()
perstokens = ConfigParser()
tokens.optionxform = str
sectokens.optionxform = str
perstokens.optionxform = str

# Commands
START_SERVER		 = "start"
STOP_SERVER			 = "stop"
RESTART_SERVER		 = "restart"
CHECK_SERVER		 = "check"
LIST_TOKENS_PERS	 = "list_pers"
LIST_TOKENS_SESSION	 = "list_session"
LIST_TOKENS_ALL		 = "list_all"
SET_TOKEN			 = "set"
SET_TOKEN_PERS		 = "set_pers"
ADD_TOKEN 			 = "add"
ADD_TOKEN_PERS		 = "add_pers"
LOAD_TOKENS 		 = "load"
LOAD_TOKENS_PERS	 = "load_pers"
LOAD_FILE 			 = "load_file"
LOAD_FILE_PERS		 = "load_file_pers"
LOAD_FILE_SEC 		 = "load_file_sec"
GET_TOKEN 			 = "get"
GET_TOKEN_PERS		 = "get_pers"
GET_TOKEN_SEC		 = "get_sec"
REMOVE_TOKEN		 = "remove"
REMOVE_TOKEN_PERS	 = "remove_pers"
REMOVE_SECTION		 = "remove_section"
REMOVE_SECTION_PERS	 = "remove_section_pers"

# Logger
log = None

# Exceptions

class ISException( Exception ):
	''' InfoStore exception. '''
	def __init__( self, code, errCause = None, errMsg = None ):
		self.code = code
		self.cause = errCause
		if errMsg == None and code < len( errStrings ):
			self.msg = errStrings[code]
		else:
			self.msg = errMsg

	def __str__( self, *args, **kwargs ):
		return "{0}{1} [{2}]: {3} : {4}".format( self.code, APPNAME,
								"ERROR" if self.code != 1 else "SUCESS", self.msg, self.cause )

programError		 = 0
success				 = 1
unknownDataFormat 	 = 2
valueNotExists		 = 3
valueAlreadyExists	 = 4
operationFailed		 = 5

errStrings = ["Program error", "Operation successfull", "Unknown data format", "Value doesn't exist", "Value already exists",
			"Operation failed"]


def signal_handler( signal, frame ):
	if server_address:
		os.unlink( server_address )
	sys.exit( 1 )

def read_conf( cnf ):
	cp = ConfigParser()
	# First reading system-wide settings
	CONFFILE = "/etc/regd/regd.conf"
	if os.path.exists( CONFFILE ):
		cp.read( CONFFILE )

	CONFFILE = homedir + "/.config/regd/regd.conf"
	if os.path.exists( CONFFILE ):
		cp.read( CONFFILE )

	for sec in cp.sections():
		for nam, val in cp.items( sec ):
			cnf[nam] = val

def read_sec_file( filename, cmd ):
	if not os.path.exists( filename ):
		log.error( "Cannot find encrypted data file. Exiting." )
		raise ISException( operationFailed, "File not found." )

	try:
		cmd = cmd.replace( "FILENAME", "{0}" )
		ftxt = subprocess.check_output( cmd.format( filename ),
							shell = True, stderr = subprocess.DEVNULL )
	except subprocess.CalledProcessError as e:
		log.error( ftxt )
		raise ISException( operationFailed, e.output )

	ftxt = ftxt.decode( 'utf-8' )
	ltxt = ftxt.split( "\n" )

	curSect = GLSEC
	rgSection = "^\[(.+)\]$"
	for s in ltxt:
		if len( s ) == 0 or s[0] == '#' or s[0] == '"':
			continue
		mSect = regex.match( rgSection, s )
		if mSect is not None:
			curSect = mSect.group( 1 )
			sectokens.add_section( curSect )
		else:
			add_token( sectokens, curSect + ":" + s )

def add_token( cp, tok, noOverwrite = False ):
	sec, _, opt = tok.rpartition( ":" )
	sec = sec.strip()
	opt = opt.strip()

	if not opt or opt.find( "=" ) == -1:
		raise ISException( unknownDataFormat, tok )

	key, _, val = opt.partition( "=" )
	key = key.strip()
	val = val.strip()

	if not sec:
		sec = GLSEC

	if not cp.has_section( sec ):
		cp.add_section( sec )

	if noOverwrite and cp.has_option( sec, key ):
		raise ISException( valueAlreadyExists, tok )

	cp[sec][key] = val

def get_token( cp, tok ):
	sec, _, opt = tok.rpartition( ":" )
	sec = sec.strip()
	opt = opt.strip()

	if not opt:
		raise ISException( unknownDataFormat, tok )

	if not sec:
		sec = GLSEC

	if not cp.has_section( sec ) or not cp.has_option( sec, opt ):
		raise ISException( valueNotExists, tok )

	return cp[sec].get( opt )

def remove_token( cp, tok ):
	sec, _, opt = tok.rpartition( ":" )
	sec = sec.strip()
	opt = opt.strip()

	if not opt:
		raise ISException( unknownDataFormat, tok )

	if not sec:
		sec = GLSEC

	if not cp.has_section( sec ) or not cp.has_option( sec, opt ):
		raise ISException( valueNotExists, tok )

	cp.remove_option( sec, opt )

def remove_section( cp, sec ):
	if not sec:
		sec = GLSEC

	if not cp.has_section( sec ):
		raise ISException( valueNotExists, sec )

	cp.remove_section( sec )

def list_tokens( cp, sec = None ):
	sects = []
	ret = ""
	if not sec:
		sects = cp.sections()
	else:
		sects += sec
	for sec in sects:
		if not cp.has_section( sec ):
			ret += "No such section: {0}\n".format( sec )
			continue
		ret += "[{0}]\n".format( sec )
		for nam, val in cp.items( sec ):
			ret += "   {0} = {1}\n".format( nam, val )

	return ret

def contactServer( item ):
	'''
	"Client" function. Performs requests to a running server.
	Called from main() when a request to the server is made via CLI.
	'''
	sock = socket.socket( socket.AF_UNIX, socket.SOCK_STREAM )
	if item.find( "_sec " ) != -1 or item.endswith( "_sec" ):
		sock.settimeout( 30 )
	else:
		sock.settimeout( 3 )

	try:
		sock.connect( server_address )
	except OSError as er:
		resp = "0regd: connectServer: Socket error {0}: {1}\nServer address: {2}".format( 
												er.errno, er.strerror, server_address )
		return resp

	try:
		# Creating packet: <data><endOfDataMarker>
		bPacket = bytearray( item + EODMARKER, encoding = 'utf-8' )

		sock.sendall( bPacket )

		eodsize = len( EODMARKER )
		while True:
			data = sock.recv( 4096 )
			datalen = len( data )
			if datalen >= eodsize and data[-eodsize:].decode( 'utf-8' ) == EODMARKER:
				break

		sock.shutdown( socket.SHUT_RDWR )
		sock.close()

		return data[:-eodsize].decode( 'utf-8' )
	except OSError as er:
		resp = "0regd: contactServer: Socket error {0}: {1}\nServer address: {2}\n".format( 
												er.errno, er.strerror, server_address )
		return resp


def startServer():

	if server_address and os.path.exists( server_address ):
		'''Socket file may remain after an unclean exit. Check if another server is running.'''
		try:
			# If the server is restarted, give the previous instance time to exit cleanly.
			time.sleep( 2 )
			res = subprocess.check_output( 
						"ps -ef | grep '{0} --start' | grep -v grep".format( __file__ ),
						shell = True )
			res = res.decode( 'utf-8' )
		except subprocess.CalledProcessError as e:
			if e.returncode != 1:
				log.error( "Check for already running server instance failed: {0} ".format( e.output ) )
				return -1
			else:
				res = ""

		if len( res ):
			if res.count( "\n" ) > 1:
				'''Server is already running.'''
				log.warning( "Server is already running:\n{0}".format( res ) )
				return 1

		try:
			os.unlink( server_address )
		except OSError:
			if os.path.exists( server_address ):
				raise

	# Default encrypted file name
	ENCFILE = homedir + "/.sec/safestor.gpg"
	# Flag showing whether the default enc. file has been read
	defencread = False
	# Command line command for reading encrypted file
	READ_ENCFILE_CMD = "gpg --textmode -d FILENAME"
	# Default persistent storage file
	PERSFILE = homedir + "/.config/regd/persistent"

	d = {}
	read_conf( d )
	if "encfile" in d:
		ENCFILE = d["encfile"]
	if "token_separator" in d:
		ITEMMARKER = d["token_separator"]
	if "encfile_read_cmd" in d:
		READ_ENCFILE_CMD = d["encfile_read_cmd"]
	if "persfile" in d:
		PERSFILE = d["persfile"]

	if os.path.exists( PERSFILE ):
		perstokens.read( PERSFILE )

	signal.signal( signal.SIGINT, signal_handler )
	signal.signal( signal.SIGTERM, signal_handler )
	signal.signal( signal.SIGHUP, signal_handler )
	signal.signal( signal.SIGABRT, signal_handler )

	eodsize = len( EODMARKER )
	cmdmarksize = len( CMDMARKER )
	itemmarksize = len( ITEMMARKER )
	log.info( "Starting server..." )
	
	try:
		if host:
			sock = socket.socket( socket.AF_INET, socket.SOCK_STREAM )
			sock.setsockopt( socket.SOL_SOCKET, socket.SO_REUSEADDR, 1 )
			sock.bind( host, port )
		else:
			sock = socket.socket( socket.AF_UNIX, socket.SOCK_STREAM )
			sock.bind( server_address )
	except OSError as e:
		print( "Cannot create or bind socket: ", e )
		return -1

	sock.listen( 1 )

	while True:
		connection, client_address = sock.accept()
		log.debug( "regd: Server: new connection" )
		connection.settimeout( 3 )
		data = bytearray()
		try:
			while True:
				data.extend( connection.recv( 4096 ) )
				datalen = len( data )
				if datalen >= eodsize and data[-eodsize:].decode( 'utf-8' ) == EODMARKER:
					break

			data = data[:-eodsize].decode( 'utf-8' )
			log.debug( "regd: Server: data received: {0}".format( data ) )

			# Three major response codes:
			# 0 - program error
			# 1 - success
			# 2 - operation unsuccessful (e.g. key doesn't exist)
			resp = "0"
			cmd = ""
			if data.startswith( CMDMARKER ):

				data = data[cmdmarksize:]
				cmd, _, data = data.partition( " " )

				if cmd == STOP_SERVER:
					resp = "1"

				if cmd == CHECK_SERVER:
					resp = "1Ready and waiting."

				if cmd == LIST_TOKENS_PERS:
					sects = data.split( "," ) if len( data ) else None
					try:
						resp = "1" + list_tokens( perstokens, sects )
					except ISException as e:
						resp = str( e )

				if cmd == LIST_TOKENS_SESSION:
					sects = data.split( "," ) if len( data ) else None
					try:
						resp = "1" + list_tokens( tokens, sects )
					except ISException as e:
						resp = str( e )

				if cmd == LIST_TOKENS_ALL:
					try:
						s = list_tokens( perstokens )
						s += list_tokens( tokens )
						resp = "1" + s
					except ISException as e:
						resp = str( e )

				elif cmd == ADD_TOKEN:
					'''Strict add: fails if the token already exists.'''
					try:
						add_token( tokens, data, noOverwrite = True )
						resp = "1"
					except ISException as e:
						resp = str( e )

				elif cmd == SET_TOKEN:
					try:
						add_token( tokens, data )
						resp = "1"
					except ISException as e:
						resp = str( e )

				elif cmd == ADD_TOKEN_PERS:
					'''Strict add: fails if the token already exists.'''
					try:
						add_token( perstokens, data, noOverwrite = True )
						with open( PERSFILE, "w" ) as f:
							perstokens.write( f, True )
						resp = "1"
					except ISException as e:
						resp = str( e )

				elif cmd == SET_TOKEN_PERS:
					try:
						add_token( perstokens, data )
						with open( PERSFILE, "w" ) as f:
							perstokens.write( f, True )
						resp = "1"
					except ISException as e:
						resp = str( e )

				elif cmd == LOAD_TOKENS:
					try:
						pl = data.find( ITEMMARKER )
						while pl != -1:
							newitem = data[:pl]
							add_token( tokens, newitem )
							data = data[( pl + itemmarksize ):]

						add_token( tokens, data )
						resp = "1"
					except ISException as e:
						resp = str( e )

				elif cmd == LOAD_TOKENS_PERS:
					try:
						pl = data.find( ITEMMARKER )
						while pl != -1:
							newitem = data[:pl]
							add_token( perstokens, newitem )
							data = data[( pl + itemmarksize ):]

						add_token( perstokens, data )
						resp = "1"
					except ISException as e:
						resp = str( e )

				elif cmd == GET_TOKEN:
					'''Get a token. '''
					try:
						resp = "1" + get_token( tokens, data )
					except ISException as e:
						resp = str( e )

				elif cmd == GET_TOKEN_PERS:
					'''Get a persistent token. '''
					try:
						resp = "1" + get_token( perstokens, data )
					except ISException as e:
						resp = str( e )

				elif cmd == GET_TOKEN_SEC:
					''' Get secure token. '''
					if not len( data ):
						resp = "0No token specified."
					else:
						try:
							if not sectokens.sections():
								'''Sec tokens are not read yet. Read the default priv. file.'''
								if not defencread:
									read_sec_file( ENCFILE, READ_ENCFILE_CMD )
									defencread = True
							resp = "1" + get_token( sectokens, data )
						except ISException as e:
							resp = str( e )

				elif cmd == LOAD_FILE:
					'''Load tokens from a file.'''
					try:
						if os.path.exists( data ):
							tokens.read( data )
							resp = "1"
						else:
							resp = "{0}File not found: {1}".format( valueNotExists, data )
					except OSError as e:
						resp = "{0}Cannot read the file: {1}".format( operationFailed, e.strerror )

				elif cmd == LOAD_FILE_PERS:
					'''Add persistent tokens from a file.'''
					try:
						if os.path.exists( data ):
							perstokens.read( data )
							resp = "1"
						else:
							resp = "{0}File not found: {1}".format( valueNotExists, data )
					except OSError as e:
						resp = "{0}Cannot read the file: {1}".format( operationFailed, e.strerror )

				elif cmd == LOAD_FILE_SEC:
					if not len( data ):
						file = ENCFILE
					else:
						file = data

					try:
						read_sec_file( file )
						resp = "1"
					except ISException as e:
						resp = str( e )

				elif cmd == REMOVE_TOKEN:
					try:
						remove_token( tokens, data )
						resp = "1"
					except ISException as e:
						resp = str( e )

				elif cmd == REMOVE_SECTION:
					try:
						remove_section( tokens, data )
						resp = "1"
					except ISException as e:
						resp = str( e )

				elif cmd == REMOVE_TOKEN_PERS:
					try:
						remove_token( perstokens, data )
						resp = "1"
					except ISException as e:
						resp = str( e )

				elif cmd == REMOVE_SECTION_PERS:
					try:
						remove_section( perstokens, data )
						resp = "1"
					except ISException as e:
						resp = str( e )

			else:
				try:
					resp = "1" + get_token( tokens, data )
				except ISException as e:
					resp = str( e )

			resp += EODMARKER

			try:
				connection.sendall( bytearray( resp, encoding = 'utf-8' ) )
			except OSError as er:
				log.error( "Socket error {0}: {1}\nClient address: {2}\n".format( 
								er.errno, er.strerror, client_address ) )

			if cmd == STOP_SERVER:
				log.info( "Stopping server." )
				if server_address:
					os.unlink( server_address )
				return 0

		finally:
			connection.shutdown( socket.SHUT_RDWR )
			connection.close()

def main():
	global homedir, server_address, log, host, port

	# Parsing command line

	class Item( argparse.Action ):
		def __init__( self, option_strings, dest, nargs = None, **kwargs ):
			if nargs is not None:
				pass
				# raise ValueError("nargs not allowed")
			super( Item, self ).__init__( option_strings, dest, nargs, **kwargs )

		def __call__( self, parser, namespace, values = None, option_string = None ):
			setattr( namespace, "itemcmd", True )
			setattr( namespace, "cmd", self.dest[:] )
			setattr( namespace, "item", values )

	class ActionCmd( argparse.Action ):
		def __call__( self, parser, namespace, values = None, option_string = None ):
			setattr( namespace, "actioncmd", True )
			setattr( namespace, "cmd", self.dest[:] )

	parser = argparse.ArgumentParser( 
		description = 'InfoStor : Secure information storage.'
	)
	group = parser.add_mutually_exclusive_group()
	parser.add_argument( 'token', nargs = '?', help = 'Get a token' )
	parser.add_argument( '--log-level', default = 'WARNING', help = 'DEBUG, INFO, WARNING, ERROR, CRITICAL' )
	parser.add_argument( '--user', help = 'User name of the server process owner.' )
	parser.add_argument( '--host', help = 'Run the server on an Internet socket with the specified hostname.' )
	parser.add_argument( '--port', help = 'Run the server on an Internet socket with the specified port.' )
	group.add_argument( '--' + START_SERVER, action = "store_true", help = 'Start server' )
	group.add_argument( '--' + STOP_SERVER, action = "store_true", help = 'Stop server' )
	group.add_argument( '--' + RESTART_SERVER, action = "store_true", help = 'Restart server' )
	group.add_argument( '--' + CHECK_SERVER, nargs = 0, action = ActionCmd, help = 'Ping server' )
	group.add_argument( '--' + ADD_TOKEN, action = Item, metavar = "TOKEN", help = 'Add a token' )
	group.add_argument( '--' + SET_TOKEN, action = Item, metavar = "TOKEN", help = 'Set a token' )
	group.add_argument( '--' + ADD_TOKEN_PERS.replace( '_', '-' ), action = Item, metavar = "TOKEN", help = 'Add a persistent token' )
	group.add_argument( '--' + SET_TOKEN_PERS.replace( '_', '-' ), action = Item, metavar = "TOKEN", help = 'Set a persistent token' )
	group.add_argument( '--' + LOAD_TOKENS.replace( '_', '-' ), action = Item, metavar = "TOKENS", help = 'Add tokens' )
	group.add_argument( '--' + LOAD_TOKENS_PERS.replace( '_', '-' ), action = Item, metavar = "TOKENS", help = 'Add persistent tokens' )
	group.add_argument( '--' + GET_TOKEN, action = Item, metavar = "NAME", help = 'Get a token' )
	group.add_argument( '--' + GET_TOKEN_PERS, action = Item, metavar = "NAME", help = 'Get a persistent token' )
	group.add_argument( '--' + GET_TOKEN_SEC.replace( '_', '-' ), action = Item, metavar = "NAME", help = 'Get a secure token' )
	group.add_argument( '--' + LIST_TOKENS_ALL.replace( '_', '-' ), action = ActionCmd, nargs = 0, help = 'List cached and persistent tokens' )
	group.add_argument( '--' + LIST_TOKENS_SESSION.replace( '_', '-' ), action = Item, metavar = "SECTIONS", nargs = '?', help = '--list-session [section[,section]...]' )
	group.add_argument( '--' + LIST_TOKENS_PERS.replace( '_', '-' ), action = Item, metavar = "SECTIONS", nargs = '?', help = '--list-pers [section[,section]...]' )
	group.add_argument( '--' + LOAD_FILE.replace( '_', '-' ), action = Item, metavar = "FILENAME", help = 'Load tokens from a file' )
	group.add_argument( '--' + LOAD_FILE_PERS.replace( '_', '-' ), action = Item, metavar = "FILENAME", help = 'Add persistent tokens from a file' )
	group.add_argument( '--' + LOAD_FILE_SEC.replace( '_', '-' ), action = Item, metavar = "FILENAME", nargs = '?', help = 'Load tokens from encrypted file' )
	group.add_argument( '--' + REMOVE_TOKEN, action = Item, metavar = "NAME", help = 'Remove a token' )
	group.add_argument( '--' + REMOVE_TOKEN_PERS.replace( '_', '-' ), action = Item, metavar = "NAME", help = 'Remove a persistent token' )
	group.add_argument( '--' + REMOVE_SECTION.replace( '_', '-' ), action = Item, metavar = "SECTION", help = 'Remove a section' )
	group.add_argument( '--' + REMOVE_SECTION_PERS.replace( '_', '-' ), action = Item, metavar = "SECTION", help = 'Remove a persistent section' )

	args = parser.parse_args()

	# Setting up local environment

	username = args.user
	if not username:
		username = os.getenv( 'LOGNAME' )

	if username:
		homedir = os.path.expanduser( '~' + username )
	else:
		homedir = ":"  # No home directory

	d = {}
	read_conf( d )
	
	# Setting up server address
	
	host = args.host
	port = args.port
	
	if ( host or port ) and not ( host and port ):
		print( ( "Error: regd not started. For running regd on an internet address both "
				"host name and port number should be specified." ) )
		return 1
	
	if not host:
		'''Server runs on a Unix file socket.'''
		sockdir = '/var/run/user/{0}'.format( pwd.getpwnam( username ).pw_uid )
		server_address = '{0}/.{1}'.format( sockdir, sockname )
	
	# Setting up logging

	log = logging.getLogger( APPNAME )
	log_level = getattr( logging, args.log_level )
	log.setLevel( log_level )

	# Console output ( for debugging )
	strlog = logging.StreamHandler()
	strlog.setLevel( log_level )
	bf = logging.Formatter( "[{asctime:s}] {module:s} {levelname:s} {funcName:s} : {message:s}", "%m-%d %H:%M", "{" )
	strlog.setFormatter( bf )
	log.addHandler( strlog )

	# File log
	if "logfile" in d:
		logfile = d["logfile"]
		if not os.path.isfile( logfile ):
			with open( logfile, 'w' ) as f:
				f.write( "" )

		filelog = logging.FileHandler( logfile )
		filelog.setLevel( logging.WARNING )
		log.addHandler( filelog )

	# Handling command line

	if not args.start:
		if not os.path.exists( server_address ):
			log.warning( "Server isn't running." )
			return 1

	if args.start:
		return startServer()

	elif args.stop:
		if contactServer( CMDMARKER + STOP_SERVER ) != "1":
			log.error( "cmd 'stop': Cannot contact server." )
			return -1

	elif args.restart:
		if contactServer( CMDMARKER + STOP_SERVER ) != "1":
			log.error( "cmd 'restart': Cannot contact server." )
			return -1

		time.sleep( 1 )

		return startServer()

	elif hasattr( args, 'itemcmd' ):
		if args.item:
			res = contactServer( CMDMARKER + args.cmd + " " + args.item )
		else:
			'''Default item'''
			res = contactServer( CMDMARKER + args.cmd )
		if res[0] != '1':
			if args.cmd.startswith( "get" ):
				print( "0", res )
			elif res[0] == '0' :
				log.error( "itemcmd: Cannot contact server." )
			else:
				log.error( "itemcmd: " + res[1:] )
			log.debug( res )
			return -1
		print( res )

	elif hasattr( args, "actioncmd" ):
		res = contactServer( CMDMARKER + args.cmd )
		if res[0] != '1':
			log.error( "actioncmd: Cannot contact server." )
			log.debug( res )
			return -1
		print( res )


	return 0


if __name__ == "__main__":
	sys.exit( main() )
