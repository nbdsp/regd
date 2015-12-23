"""******************************************************************
*	Module:			regd
*
*	File name:		cmds.py
*
*	Created:		2015-12-16 07:26:35
*
*	Abstract:		Server commands syntax definitions.
*
*	Author:			Albert Berger [ alberger@gmail.com ].
*
*******************************************************************"""

__lastedited__ = "2015-12-23 01:13:45"

from regd.app import IKException, ErrorCode
import regd.defs as df
import regd.util as util

class Cmd():
	'''Server command'''
	def __init__(self, name:str, npars:str, required:set, optional:set, func):
		self.name = name
		self.npars = npars
		self.required = required if required else set()
		self.optional = optional if optional else set()
		self.func = func
	
	def check(self, dcmd):
		'''Check the command dictionary'''

		# Name
		if dcmd["cmd"] != self.name:
			raise IKException( ErrorCode.nameError, dcmd["cmd"] )

		# Numbers of parameters
		if self.npars != "*":
			parlen = len( dcmd["params"] )
			wrong = False
			if self.npars[-1] == "+":
				n = int( self.npars[:-1] )
				if parlen < n: wrong = True
			elif self.npars == "?":
				if parlen > 1: wrong = True
			elif parlen != int( self.npars ):
				wrong = True
			if wrong:
				raise IKException( ErrorCode.unrecognizedSyntax, parlen, "Wrong number of parameters", "Expected: {0}".format( self.npars ) )

		comargs = ("cmd", "params", df.LOG_LEVEL, df.LOG_TOPICS, df.SERVER_NAME, df.HOST, df.PORT)

		cargs = set([x for x in dcmd if x not in comargs])

		# Required arguments
		
		if not self.required.issubset( cargs ):
			raise IKException( ErrorCode.unrecognizedSyntax, self.required - cargs, "Some required command arguments are missing" )

		# Optional arguments
		red = cargs - ( self.required | self.optional )
		if red:
			raise IKException( ErrorCode.unrecognizedSyntax,red, "Some command arguments are not recognized" )

	def __call__( self, cmd ):
		self.check( cmd )
		return self.func( cmd )
	
class CmdProcessor:
	'''Mixin base for classes with command handlers members'''
	
	"""The CmdProcessor object may function as a command processing server in a
	separate process or as a common object some of whose methods serve as command 
	handlers. In the first case the CmdProcessor object must be provided with a 
	Connection endpoint, for receiving commands and a forwarder function with other
	endpoint, which routes commands to the processor, must be registered as a group
	command handler in the program's CmdSwitcher."""
	
	# List of command definitions. Must be overrided in subclasses
	cmdDefs = None
	
	def __init__( self, conn=None ):
		self.cmdMap = {}
		self.conn = conn
		
		for c in self.cmdDefs:
			self.addHandler( c )
		
	def addHandler( self, cmdDef ):
		'''Add handler for a command'''
		cmd = Cmd( name=cmdDef[0], npars=cmdDef[1], required=cmdDef[2], optional=cmdDef[3], 
				func = getattr(self, cmdDef[4]) )
		self.cmdMap[cmd.name] = cmd

	def processCmd(self, cmd):
		'''Process command'''
		if cmd["cmd"] not in self.cmdMap:
			util.composeResponse( '0', "Unrecognized command: ", cmd["cmd"] )

		CmdSwitcher.info["cmds"][cmd["cmd"]] = 1 + CmdSwitcher.info["cmds"].setdefault(cmd["cmd"], 0)
		hnd = self.cmdMap[cmd["cmd"]]
		return hnd( cmd )

	def listenForMessages(self, func):
		while True:
			if self.conn.poll(30):
				cmd = self.conn.recv()
				resp = self.processCmd( cmd )
				self.conn.send( resp )
			else:
				func()
	
	@classmethod
	def registerGroupHandlers( cls, func ):
		cmdNames = [x[0] for x in cls.cmdDefs]
		registerGroupHandler(cmdNames, func )
		
class CmdSwitcher:
	'''Docstring for CmdSwitcher.'''
	cmdHandlers = {}
	info = {}
	info["cmds"] = {}

	@staticmethod
	def addGroupHandler( cmdNames, func ):
		'''Add handler for a command group'''
		for c in cmdNames:
			CmdSwitcher.cmdHandlers[c] = func
		
	@staticmethod
	def switchCmd( cmd ):
		'''Switch command'''
		if cmd["cmd"] not in CmdSwitcher.cmdHandlers:
			return util.composeResponse( '0', "Unrecognized command: ", cmd["cmd"] )

		CmdSwitcher.info["cmds"][cmd["cmd"]] = 1 + CmdSwitcher.info["cmds"].setdefault(cmd["cmd"], 0)
		hnd = CmdSwitcher.cmdHandlers[cmd["cmd"]]
		return hnd( cmd )


def registerGroupHandler( cmdNames : list, func ):
	'''Register command handler'''
	CmdSwitcher.addGroupHandler( cmdNames, func )


