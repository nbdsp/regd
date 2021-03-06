'''
/********************************************************************
*	Module:			regd.stor
*
*	Created:		Jul 5, 2015
*
*	Abstract:		Storage.
*
*	Author:			Albert Berger [ alberger@gmail.com ].
*
*********************************************************************/
'''
__lastedited__ = "2016-07-23 07:18:04"

import sys, re, os, time, threading, shutil
from enum import Enum
from regd.util import log, logtok
from regd.appsm.app import IKException, ErrorCode
import regd.defs as defs
import regd.util as util
import regd.appsm.app as app
from regd.defs import dirInclude
from regd.tok import parse_token, stripOne
from regd.util import logsr
from collections import deque, OrderedDict

from errno import ENOENT
from stat import S_IFDIR, S_IFLNK, S_IFREG
from io import SEEK_SET

SECTIONPATT = "^\[(.*)\]$"
SECTIONNAMEPATT = "^(.*?)(?<!\\\):(.*)"
TOKENPATT = "^(.*?)(?<!\\\)=(.*)"
MULTILINETOKENPATT = "^[ \t](.*)$"
NUMBERPATT = "\-?([0-9]*\.)?[0-9]+"
DATADIRECTPATT = "^({0}) (.*)$".format( defs.dirInclude )

reNumber = re.compile( NUMBERPATT )
reSect = re.compile( SECTIONPATT )
reDataDirect = re.compile( DATADIRECTPATT )
reTok = re.compile( TOKENPATT, re.DOTALL )
reMlTok = re.compile( MULTILINETOKENPATT )
reEmpty = re.compile( "^\r?\n?$" )

changed = []
lock_changed = threading.Lock()
treeLoad = False

class EnumMode( Enum ):
	both		 = 1
	tokens		 = 2
	sections 	 = 3
	all 		 = 4
	tokensAll	 = 5
	sectionsAll	 = 6

class st_struct:
	'''stat struct'''
	def __init__( self, mode = None, uid = None, gid = None, ctime = None, mtime = None, atime = None,
				nlink = None ):
		nowtm = time.time()
		self.st_mode = mode if mode else 0o644
		self.st_uid = uid if uid else os.getuid()
		self.st_gid = gid if gid else os.getgid()
		self.st_ctime = ctime if ctime else nowtm
		self.st_mtime = mtime if mtime else nowtm
		self.st_atime = atime if atime else nowtm
		self.st_nlink = nlink if nlink else 1

class SItem:
	'''Storage item'''
	class Attrs( Enum ):
		persPath 	 = 0  # Persistent storage path
		encoding	 = 1
		compressed 	 = 2

	# stor.SItem.Attrs.persPath.name => stor.SItem.persPathAttrName
	persPathAttrName = Attrs.persPath.name

	def __init__( self, itype, rootStor, mode = None, uid = None, gid = None, ctime = None, mtime = None, atime = None,
				nlink = None, name = '', attrs = None ):
		self.name = name
		self.rootStor = rootStor
		self.itype = itype
		self.st = st_struct( mode, uid, gid, ctime, mtime, atime, nlink )
		self.attrs = attrs if attrs else {}
		self.storageRef = None
		self.changed = False
		return super( SItem, self ).__init__()
	
	def value(self):
		raise NotImplementedError()

	def getAttr( self, attrName = None ):
		if not attrName:
			st_mode = ( self.itype | self.st.st_mode )
			ret = {'st_mode':st_mode, 'st_uid':self.st.st_uid, 'st_gid':self.st.st_gid,
				'st_dev':0, 'st_rdev':0, 'st_ino':0, 'st_size':self.getsize(),
				'st_blksize':2048, 'st_blocks':( int( self.getsize() / 512 ) ) + 1,
				'st_ctime':int( self.st.st_ctime ),
				'st_atime':int( self.st.st_atime ), 'st_mtime':int( self.st.st_mtime ) }
			return ret
		if attrName == "Extended":
			return self.attrs
		elif attrName not in self.attrs:
			return None
		return self.attrs[attrName]

	def setAttr( self, attrName, attrVal ):
		if attrName == 'st_mode':
			self.st_mode = attrVal
		else:
			self.attrs[attrName] = attrVal
			if attrName == SItem.persPathAttrName:
				self.setStorageRef( self )

	def setAttrs( self, lattrs ):
		for x in lattrs:
			k, _, v = x.strip( ' ' ).partition( '=' )
			k = k.strip()
			v = v.strip()
			if k == 'st_mode':
				self.st_mode = v
			else:
				self.attrs[k] = v
				if k == SItem.Attrs.persPath.name:
					self.setStorageRef( self )
		return self
		# self.markChanged()

	def isWritable(self):
		return self.st.st_mode & 0o200 == 0o200

	def setStorageRef( self, ref ):
		if self.storageRef and self.storageRef != ref:
			self.storageRef.markChanged()
		self.storageRef = ref

	def setName( self, name ):
		self.name = name

	def markChanged( self ):
		if treeLoad:
			return
		self.changed = True
		if self.storageRef != None and self.storageRef != self:
			self.storageRef.markChanged()
		else:
			with lock_changed:
				changed.append( self )

	def getsize( self ):
		pass

	def getSerializationFile( self, fhd, read, fpath = None ):
		logsr.debug( "getSerFile() is called..." )
		fhcur = fhd.get( "cur", None )
		if not fpath:
			if self.storageRef == None:
				raise IKException( ErrorCode.valueNotExists, self.pathName(), "No storage for" )
			fname = self.storageRef.getAttr( SItem.Attrs.persPath.name )
			if fhcur:
				fpath = self.getPersStoragePath( os.path.dirname( fhcur.name ) )
			else:
				fpath = fname
		if not fpath:
			raise IKException( ErrorCode.valueNotExists, self.name, "Storage not specified for" )
		sfx = "" if read else ".tmp"

		if fpath + sfx in fhd:
			fh = fhd[fpath + sfx]
			if fh == fhcur:
				logsr.debug( "getSerFile() returns file: {0}".format( fh.name ) )
				return fh
		else:
			if not os.path.isabs( fpath ):
				if not fhcur:
					raise IKException( ErrorCode.valueNotExists, fpath, "Cannot resolve relative path" )
				fpath = os.path.normpath( os.path.join( os.path.dirname( fhcur.name ), fpath ) )

			logsr.debug( "getSerFile() opens file {0}".format( fpath ) )
			if read:
				fh = open( fpath, "rb" )
			else:
				# if os.path.isfile(fpath):
				# 	shutil.move(fpath, fpath + ".backup~")
				fh = open( fpath + sfx, "wb" )
			fhd[fpath + sfx] = fh

		if not read and fhcur and isinstance( self, Stor ):
			dinc = "\n" + dirInclude + " " + fname + " " + self.name + "\n\n"
			logsr.debug( "getSerFile() writes to fhcur {0}: {1}".format( fhcur, dinc ) )
			fhcur.write( dinc.encode() )

		if not fh:
			raise IKException( ErrorCode.programError, self.name, "No storage file" )
		logsr.debug( "getSerFile() returns file: {0}".format( fh.name ) )
		return fh

	def isPersStorage( self ):
		if SItem.persPathAttrName in self.attrs:
			return True
		return False

	def getPersStoragePath( self, curDir ):
		fpath = self.storageRef.getAttr( SItem.persPathAttrName )
		if not fpath:
			return None
		if not os.path.isabs( fpath ):
			fpath = os.path.normpath( os.path.join( curDir, fpath ) )
		return fpath

	def readFromFile( self, fh = None, updateFromStorage = False, filePath = None, 
					dest = None, addMode = defs.overwrite ):
		'''Read from one of the three: an open file objectl, a file named in filePath 
		or update from the backing storage.'''
		fhd = {}
		if updateFromStorage:
			if SItem.persPathAttrName in self.attrs:
				self.clear()
				fpath = self.attrs[SItem.persPathAttrName]
		elif filePath: 
			fpath = filePath
		elif fh:
			fpath = fh.name
			fhd[fh.name] = fh
			fhd['cur'] = fh
		
		self.serialize( fhd = fhd, read = True, addMode = addMode, fpath = fpath, 
					relPath = dest )
		for fh in fhd.values():
			fh.close()

	def writeToFile( self, updateStorage = True, fpath = None ):
		fpaths = []
		if fpath: fpaths.append( fpath )
		if updateStorage:
			fpaths.append( self.attrs[SItem.persPathAttrName] )
		for f in fpaths:
			fhd = {}
			self.serialize( fhd = fhd, read = False, fpath = f )
			for fpath, fh in fhd.items():
				fh.close()
				shutil.move( fpath, fpath[:-4] )

class SVal( SItem ):
	'''Storage value container.'''
	def __init__( self, rootStor, val = None, mode = None, uid = None, gid = None, ctime = None, mtime = None, atime = None,
				nlink = None, name = '', attrs = None ):
		self.val = val
		# Reference to the containing stor
		self.stor = None
		return super( SVal, self ).__init__( S_IFREG, rootStor, mode, uid, gid, ctime, mtime, atime,
										nlink, name = name, attrs = attrs )

	def __str__( self ):
		if type( self.val ) is bytes or type( self.val ) is bytearray:
			return self.val.decode( 'utf-8', errors = 'surrogateescape' )
		elif type( self.val ) is str:
			return self.val
		else:
			return str ( self.val )
		
	def value(self):
		return self.val
		
	def getsize( self ):
		return len( self.val ) if self.val else 0

	def clear( self ):
		'''Counterpart of Stor's clear().'''
		self.val = None

	def pathName( self ):
		return self.stor.pathName() + "/" + self.name
	
	def stat(self):
		return { "size" : self.getsize() }

	def serialize( self, fhd, read = True, fpath = None ):
		'''Serialization (writing) for storage values'''
		logsr.debug( "SVal.ser() is called..." )
		fh = self.getSerializationFile( fhd, read, fpath )
		if read:
			'''Returns:
			0 - EOF
			1 - OK
			2 - Non-token line
			'''
			tok = b""
			val = b""
			attrs = None
			continued = ""
			binaryVal = None
			while True:
				origPos = fh.tell()
				l = fh.readline().rstrip( b'\n' )
				if l:
					if l[-1] == '\\':
						continued += l[:-1]
						continue
					if continued:
						l = continued
						continued = ""

				if l and ( l[0] == ord(' ') or l[0] == ord('\t') ):
					if not tok:
						raise IKException( ErrorCode.unknownDataFormat, l, "Cannot parse token." )
					tok += b" " + l[1:]
				elif tok:
					if val:
						tok += val
						val = b""
					mtok = reTok.match( tok.decode( errors = "surrogateescape" ) )
					if not mtok or mtok.lastindex != 2:
						raise IKException( ErrorCode.unknownDataFormat, l, "Cannot parse token" )
					tok = tok.replace( b'\n\t', b'\n' )
					enc = "utf-8"
					compr = False
					binaryVal = None
					if attrs:
						enc = attrs.get( "encoding", enc )
						compr = bool( attrs.get( "compression", str( compr ) ) )
					if enc != "binary":
						tok = tok.decode( enc, errors = "surrogateescape" )
					else:
						binaryVal = tok
						tok = ""
					fh.seek( origPos, SEEK_SET )
					break
				elif l.startswith( b"//attributes " ):
					if attrs:
						raise IKException( ErrorCode.unrecognizedParameter, tok, "Unknown data file format" )
					_, _, s = l.decode().partition( " " )
					lattrs = s.split( " " )
					attrs = {}
					for x in lattrs:
						k, _, v = x.partition( "=" )
						if not ( k and v ):
							raise IKException( ErrorCode.unrecognizedSyntax, x,
											"Unrecognized syntax for an attribute" )
						attrs[k] = v
						if k == SItem.persPathAttrName:
							if not os.path.isabs( v ):
								v = os.path.normpath( os.path.join( os.path.dirname( fh.name ), v ) )
							with open( v, "rb" ) as f:
								val = f.read()
					logsr.debug( "SVal.ser() reads attributes: {0}".format( s ) )
					tok = b""
				elif l.startswith( b'//' ):
					fh.seek( origPos, SEEK_SET )
					logsr.debug( "SVal.ser() returns 2" )
					return 2
				elif l:
					tok = l
				else:
					# EOF
					logsr.debug( "SVal.ser() returns 0" )
					return 0

			if not binaryVal:
				path, val = parse_token( tok, bVal=defs.yes )
			else:
				path, _ = parse_token( tok, bVal=defs.no )
				val = binaryVal
			self.name = path[-1]
			self.attrs = attrs if attrs else {}
			self.val = val
			logsr.debug( "SVal.ser() reads token: {0}={1}".format( path[-1], val ) )
			return 1

		else:
			# if SVal has separate storage, fh points to it and fhcur to stor's storage;
			# otherwise, fh and fhcur are the same
			fhcur = fhd.get( "cur", fh )
			def writeAttrs( fh, attrs ):
				'''Writes token attributs to data file at the current file position.'''
				if attrs:
					fhcur.write( b"//attributes" )
					s = ""
					for k, v in attrs.items():
						s += " {0}={1}".format( k, v )
					fhcur.write( s.encode() )
					fhcur.write( b'\n' )
					logsr.debug( "SVal.ser() writes to fhcur ({0}): attributes {1}".format( 
																	fhcur.name, s ) )

			writeAttrs( fhcur, self.attrs )
			if self.attrs:
				enc = self.attrs.get( "enc", "utf-8" )
			else:
				enc = "utf-8"			
			
			if type( self.val ) is bytes or type( self.val ) is bytearray:
				pass
			elif type( self.val ) is str:

				v = self.val.encode( enc )
			else:
				v = repr( self.val ).encode()
			v = v.replace( b'\n', b'\n\t' )
			logsr.debug( "SVal.ser() writes to fhcur({0}) token name: {1}".format( fhcur.name,
																		self.name ) )
			fhcur.write( "{0}=".format( self.name ).encode( enc ) )
			logsr.debug( "SVal.ser() writes to fhcur({0}) token value: {1}".format( fhcur.name,
																		v ) )
			fh.write( v )
			fhcur.write( b'\n' )
			self.changed = False

class Stor( SItem, dict ):
	'''Section'''
	def __init__( self, rootStor, name = '', mode = None, uid = None, gid = None, ctime = None, mtime = None, atime = None,
				nlink = None ):
		self.path = ''
		self.enumMode = None
		return super( Stor, self ).__init__( S_IFDIR, rootStor, mode, uid, gid, ctime, mtime, atime,
										nlink, name = name )

	def __getitem1__( self, key ):
		item = super( Stor, self ).__getitem__( key )
		if isinstance( item, baseSectType ):
			return item
		elif isinstance( item, baseValType ):
			return item.val
		else:
			raise IKException( ErrorCode.programError, "Storage item type is: {0}".format( str( type( item ) ) ) )

	def get( self, *args, **kwargs ):
		return dict.get( self, *args, **kwargs )

	def __setitem__( self, key, val ):
		if not hasattr( self, "name"):
			return dict.__setitem__( self, key, val)
		if self.name and not key:
			raise IKException( ErrorCode.unrecognizedParameter, "section name is empty" )
		if key:
			if "/" in key:
				raise IKException( ErrorCode.unrecognizedParameter, "section name contains '/'" )
			if key == '.':
				raise IKException( ErrorCode.unrecognizedParameter, key, "not valid section name" )
		if type( val ) == type( self ):
			if self.path:
				val.setPath( self.path + "/" + self.name )
			else:
				if self.name:
					val.setPath( "/" + self.name )
				else:
					val.setPath( '' )
		else:
			if not isinstance( val, SVal ):
				val = SVal( self.rootStor, val=val, uid=self.st.st_uid, gid=self.st.st_gid )
			val.stor = self

		val.setName( key )
		if val.storageRef is None:
			val.setStorageRef( self.storageRef )

		return super( Stor, self ).__setitem__( key, val )

	def __delitem__( self, key ):
		return super( Stor, self ).__delitem__( key )
	
	'''def __setstate__(self, state):
		oldf = self.__setitem__
		#self.__setitem__ = dict.__setitem__
		self.__dict__.update( state )
		self.__setitem__ = self.__setitem1__'''

	def __contains__( self, key ):
		
		return super( Stor, self ).__contains__( key )
	
	def value(self):
		'''Convert the section to dict.'''
		
		ret = {}
		for k,v in self.items():
			ret[k] = v.value()
			
		return ret
	
	def appendDict( self, d, key=None, addMode=defs.noOverwrite ):
		'''Convert a dictionary to a subsection'''
			
		if key:
			if key not in self or addMode == defs.overwrite:
				self[key] = getstor( rootStor = self.rootStor, mode=self.st.st_mode )
			self[key].appendDict( d=d, addMode=addMode )
		else:
			if addMode == defs.clean:
				self.clear()
			for k,v in d.items():
				if isinstance( v, dict ):
					if k not in self or addMode == defs.overwrite:
						self[k] = getstor( rootStor = self.rootStor, mode=self.st.st_mode )
					self[k].appendDict( d=v, addMode=addMode )
				else:
					self.insertNamVal( nam=k, val=v, addMode=addMode )

	class StorIter:
		def __init__( self, stor, mode ):
			self.mode = mode
			self.secdeq = deque()
			self.it = iter( stor )
			self.cd = stor  # current dict
			self.tp = type( stor )  # section type

		def __next__( self ):
			while 1:
				try:
					item = next( self.it )
				except StopIteration:
					if len( self.secdeq ):
						self.cd = self.secdeq.popleft()
						self.it = iter( self.cd )
						continue
					else:
						raise

				if isinstance( self.cd[item], self.tp ):
					if self.mode in ( EnumMode.all, EnumMode.tokensAll, EnumMode.sectionsAll ):
						self.secdeq.append( self.cd[item] )
					if self.mode in ( EnumMode.all, EnumMode.sections, EnumMode.sectionsAll ):
						# return None, self.cd[item].pathName()
						return item, self.cd[item]
				else:
					if self.mode not in ( EnumMode.sections, EnumMode.sectionsAll ):
						return item, self.cd[item]

	def __iter__( self, *args, **kwargs ):
		if self.enumMode == None:
			return dict.__iter__( self, *args, **kwargs )
		em = self.enumMode
		self.enumMode = None
		it = Stor.StorIter( self, em )

		return it

	def getsize( self ):
		return 0

	def enumerate( self, mode ):
		self.enumMode = mode

	def setPath( self, path ):
		self.path = path
		for v in self.values():
			if self.path:
				v.setPath( self.path + "/" + self.name )
			else:
				if self.name:
					v.setPath( "/" + self.name )
				else:
					raise IKException( ErrorCode.programError, "Storage item has no name." )

	def setStorageRef( self, ref ):
		super( Stor, self ).setStorageRef( ref )
		for v in self.values():
			v.setStorageRef( ref )

	def pathName( self ):
		# if self.path:
		return self.path + "/" + self.name
		# else:
		# 	return self.name

	def numItems( self, itemType = EnumMode.both ):
		cnt = None
		if itemType == EnumMode.both:
			cnt = len( self )
		elif itemType in ( EnumMode.tokens, EnumMode.tokensAll ):
			cnt = 0
			for k in self.keys():
				if type( self[k] ) != type( self ):
					cnt += 1
				elif itemType == EnumMode.tokensAll:
					cnt += self[k].numItems( EnumMode.tokensAll )

		elif itemType in ( EnumMode.sections, EnumMode.sectionsAll ):
			cnt = 0
			for k in self.keys():
				if type( self[k] ) == type( self ):
					cnt += 1
					if itemType == EnumMode.sectionsAll:
						cnt += self[k].numItems( EnumMode.sectionsAll )
		elif itemType == EnumMode.all:
			cnt = self.numItems( EnumMode.tokensAll ) + self.numItems( EnumMode.sectionsAll )
		else:
			raise IKException()
		return cnt

	def getItemAttr( self, path, attrName = None ):
		item = self.getItem( path )
		return item.getAttr( attrName )

	def setItemAttr( self, path, lattrs ):
		item = self.getItem( path )
		return item.setAttrs( lattrs )

	def addItem(self, tok : str=None, path : list=None,	val=None, binaryVal=None, 
			attrs=None, addMode=defs.noOverwrite ):
		'''Add a token or section to stor'''
		
		if tok:
			if not binaryVal:
				path, val = parse_token( tok, bVal = defs.auto )
			else:
				path, _ = parse_token( tok, bVal = defs.no )
				val = binaryVal
		
		else:
			if val and binaryVal:
				raise IKException( ErrorCode.unsupportedParameterValue, val, "Both value and binary value are specified" )
			if binaryVal:
				val = binaryVal
				
		if not path:
			raise IKException( ErrorCode.unknownDataFormat, val, "Token name is not specified" )
		
		if val:
			# Storage contains items of two types: SVal and Stor: 
			if not isinstance( val, SVal ) and not isinstance( val, dict ):
				nam = path[-1]
				val = SVal( rootStor = self.rootStor, name = nam, attrs = attrs, val = val )
		
		return self._addItem( path=path, val=val, attrs=attrs, addMode=addMode )
			
	def _addItem(self, path : list=None, val : SVal=None, attrs=None, addMode=defs.noOverwrite ):
		if not path:
			raise IKException( ErrorCode.unknownDataFormat, val, "Token path is not specified")

		if len( path ) == 1 and not self.isWritable():
			print( "Directory permission: {0}".format( oct( self.st.st_mode ) ) )
			raise IKException( ErrorCode.permissionDenied, self.pathName(), "Directory is not writeable" )

		curname = path[0]

		if curname not in self.keys() and ( not val or len(path) > 1 ):
			self[curname] = getstor( rootStor = self.rootStor, mode=self.st.st_mode )
			
		if len( path ) == 1:
			if val:
				return self.insertNamVal( curname, val, addMode, attrs)
			return self[curname]
			
		return self[curname]._addItem( path=path[1:], val=val, attrs=attrs, addMode=addMode )
		
	def insertNamVal( self, nam, val, addMode = defs.noOverwrite, attrs = None ):
		if isinstance( val, dict ):
			self.appendDict(val, nam, addMode)
		else:
			if nam in self.keys():
				if addMode == defs.noOverwrite:
					raise IKException( ErrorCode.valueAlreadyExists, nam )
				if addMode == defs.sumUp:
					s = self[nam].value()
					val = val.value()
					if reNumber.match( s ) and reNumber.match( val ):
						if '.' in s or '.' in val:
							val = str( float( s ) + float( val ) )
						else:
							val = str( int( s ) + int( val ) )
					else:
						val = s + val
				if not isinstance( val, SVal ):
					val = SVal( rootStor = self.rootStor, name = nam, attrs = attrs, val = val )
			self[nam] = val
		return self
		# self.markChanged()
		
	def getItem(self, path):
		'''Return a token or section with the given path.'''
		
		if type( path ) is str:
			lpath, _ = parse_token( path, bVal = defs.auto )
			return self.getItem( lpath )
		else:
			if path:
				if path[0] not in self.keys():
					raise IKException( ErrorCode.objectNotExists, path[0], "Item doesn't exist" )
				if len( path ) > 1:
					return self[path[0]].getItem( path[1:] )
				else:
					return self[path[0]]
			
			raise IKException( ErrorCode.nameError, "getItem: path is empty" )
			return self	

	def removeItem( self, tok ):
		path, _ = parse_token( tok , bVal = defs.auto )
		if len( path ) == 1: 
			if not ( self.isWritable() and self[path[0]].isWritable() ) :
				raise IKException( ErrorCode.permissionDenied, tok, "Item is read-only")
			del self[path[0]]
			return self
		
		sec = self.getItem( path[:-1] )
		return sec.removeItem( path[-1] )
		
	def copy( self, src, dst, addMode=defs.noOverwrite ):
		sitem = self.getItem( src )
		self.addItem( tok=dst, binaryValue=sitem, addMode=addMode )

	def rename( self, src, dst, addMode=defs.overwrite ):
		self.copy( src, dst )
		self.removeItem( src )

	def listItems( self, lres, bTree = False, nIndent = 0, bNovals = True, relPath = None, bRecur = True ):
		'''This function with btree=False, bNovals=False is used also for writing
		persistent tokens.'''
		if lres == None:
			return

		pathPrinted = False if bRecur else True

		if bTree:
			# First listing tokens
			for nam, val in self.items():
				if type( val ) != type( self ):
					if bNovals:
						line = "{0}- {1}".format( ' ' * nIndent, nam )
					else:
						line = "{0}- {1}  : {2}".format( ' ' * nIndent, nam, val )
					lres.append( line )

			# Then sections
			for nam, val in self.items():
				if type( val ) == type( self ):
					line = "{0}[{1}]:".format( ' ' * nIndent, nam )
					lres.append( line )
					if bRecur:
						val.listItems( lres = lres, bTree = bTree, nIndent = nIndent + 4,
									bNovals = bNovals, relPath = relPath, bRecur = bRecur )

		else:
			if relPath != None:
				if relPath == '':
					relPath = '.'
				elif relPath == '.':
					relPath = self.name
				else:
					relPath = relPath + "/" + self.name

			for nam, val in self.items():
				if type( val ) != type( self ):
					if not pathPrinted:
						lres.append( "" )
						if relPath:
							if relPath != '.':
								lres.append( "[" + relPath + "]" )
						else:
							lres.append( "[" + self.pathName() + "]" )
						pathPrinted = True

					if bNovals:
						line = "{0}".format( nam )
					else:
						nam = nam.replace( "=", "\\=" )
						line = "{0} = {1}".format( nam, val )
					lres.append( line )
			# res.append("")
			for nam, val in self.items():
				if type( val ) == type( self ):
					if bRecur:
						val.listItems( lres, bTree, 0, bNovals, relPath, bRecur )
					else:
						lres.append( "[" + val.name + "]" )

	def getTokensList( self, lres ):
		for n, v in self.items():
			if type( v ) == type( self ):
				v.getTokensList( lres )
			else:
				lres.append( self.path + "/" + self.name + "/" + n + "=" + v )

	def serialize( self, fhd, read = True, addMode = defs.noOverwrite, relPath = None, 
				fpath = None, indent = 0 ):
		if self.storageRef and self.storageRef != self:
			raise IKException( ErrorCode.programError, self.pathName(), "serialize() called on non-root of backing storage." )

		if read == True:
			if addMode == defs.clean:
				self.clear()

			oldRoot = self.rootStor
			self.rootStor = self
			self._serialize( fhd=fhd, read=read, addMode=addMode, relPath=relPath,
								fpath=fpath )
			self.rootStor = oldRoot
		else:
			self._serialize( fhd=fhd, read=read, addMode=addMode, relPath=relPath,
								fpath=fpath )

	def _serialize( self, fhd, read = True, addMode = defs.noOverwrite, relPath = None, 
				fpath = None, indent = 0 ):
		'''Reads tokens from or writes to backing storage.'''
		logsr.debug( "Stor.ser() is called..." )
		if relPath and relPath[-1] != '/': relPath += '/'
		curPath = relPath if relPath else ""
		fh = None

		def handleDataDirective( direc, val ):
			'''Handles data directives.'''
			logsr.debug( "handleDataDirective() is called..." )
			nonlocal fhd, read, addMode
			if direc == defs.dirInclude:
				largs = app.splitArgs( val )
				if len( largs ) == 2:
					file, sect = largs
				else:
					file = largs[0]
					sect = None
				fname = file.strip( ' \n' )
				if not os.path.isabs( fname ):
					if not sect: sect = fname
					fpath = os.path.normpath( os.path.join( os.path.dirname( fh.name ), fname ) )
				else:
					fpath = fname
				if not os.path.isfile( fpath ):
					raise IKException( ErrorCode.objectNotExists, fpath, "Included file not found:" )
				if fpath in fhd:
					# File is read more than once.
					# fh = fhd[fpath]
					raise IKException( ErrorCode.valueAlreadyExists, fpath, "Data file is referenced more than once" )

				logsr.debug( "haDaDir() opens file {0}".format( fpath ) )
				fhd[fpath] = open( fpath, "rb" )
				logsr.debug( "haDaDir() creates section {0}".format( 
												self.rootStor.pathName() + "/" + sect ) )
				sec = self.rootStor.addItem( tok=sect )
				logsr.debug( "haDaDir() sets section's persPath to {0}".format( fpath ) )
				sec.setAttr( SItem.Attrs.persPath.name, fname )
				logsr.debug( "haDaDir() calls section's serialize()" )
				sec._serialize( fhd = fhd, read = read, addMode = addMode )

		def readNonToken( continued = None ):
			'''Reads non-token lines: section names, data directives'''
			logsr.debug( "readNonToken is called..." )
			nonlocal fh, curPath
			origPos = fh.tell()
			l = fh.readline().decode().strip( '\n' )
			if continued:
				if l[-1] == '\\':
					continued += l[:-1]
					return readNonToken( continued )
				continued += l
				return continued

			m = reSect.match( l )
			if m:
				curPath = ( relPath if relPath else "" ) + m.group( 1 )
				if curPath[-1] != '/': curPath += "/"
				logsr.debug( "rNoTok() sets the curDir to {0} and returns".format( curPath ) )
				return True

			m = reDataDirect.match( l )

			if m:
				direc = m.group( 1 )
				val = m.group( 2 )
				if l[-1] == '\\':
					val = self.readNonToken( val )
				logsr.debug( "rNoTok() reads datadir {0} and calls haDaDir".format( l ) )
				handleDataDirective( direc, val )
			else:
				fh.seek( origPos, SEEK_SET )
				return False

			return True

		def readEmptyLines():
			nonlocal fh
			while True:
				origPos = fh.tell()
				l = fh.readline().decode()
				if not reEmpty.match( l ) or not l:
					fh.seek( origPos, SEEK_SET )
					break

		fh = self.getSerializationFile( fhd, read, fpath )
		fhd["cur"] = fh

		if read:
			while True:
				logsr.debug( "New while iteration" )
				readEmptyLines()
				while readNonToken():
					readEmptyLines()
				# readToken()
				tok = SVal( rootStor = self.rootStor, val = None, mode = 0o644, 
						uid = self.st.st_uid, gid = self.st.st_gid )
				tok.setStorageRef( self.storageRef )
				fhd["cur"] = fh
				res = tok.serialize( fhd, read, fpath )
				if res == 0:
					break
				if res == 1:
					path = [x for x in curPath.split( "/" ) if x]
					path.append( tok.name )
					if not self.name:
						path = [''] + path
					self.addItem( addMode = addMode, path = path, val = tok )
		else:
			if SItem.persPathAttrName in self.attrs:
				relPath = ""
				secpath = ""
			else:
				secpath = ( relPath if relPath else "" ) + self.name
				relPath = secpath

			logsr.debug( "{0}Serializing: Stor {1}".format( " "*indent, self.pathName() ) )
			if self.numItems( EnumMode.tokens ):
				logsr.debug( "{0}Stor {1}: writing items".format( " "*indent, self.pathName() ) )
				if secpath:
					fh.write( "\n[{0}]\n\n".format( secpath ).encode() )

				self.enumerate( EnumMode.tokens )
				for nam, _ in self:
					fhd["cur"] = fh
					logsr.debug( "{0}Stor {1}: writing item {2}".format( " "*indent, self.pathName(), nam ) )
					self.get( nam ).serialize( fhd, read )

			self.enumerate( EnumMode.sections )
			logsr.debug( "{0}Stor {1}: writing sections".format( " "*indent, self.pathName() ) )
			for _, v in self:
				fhd["cur"] = fh
				logsr.debug( "{0}Stor {1}: writing section {2}".format( " "*indent, self.pathName(), v.pathName() ) )
				v._serialize( fhd, read = read, relPath = relPath, indent = indent + 4 )

			self.changed = False

	def stat( self ):
		m = OrderedDict()
		m['num_of_sections'] = self.numItems( EnumMode.sectionsAll )
		m['num_of_tokens'] = self.numItems( EnumMode.tokensAll )
		m['max_key_length'] = 0
		m['max_value_length'] = 0
		m['avg_key_length'] = 0
		m['avg_value_length'] = 0
		m['total_size_bytes'] = 0
		self.enumerate( EnumMode.tokensAll )
		for nam, val in self:
			if len( nam ) > m['max_key_length']:
				m['max_key_length'] = len( nam )
			if len( val ) > m['max_value_length']:
				m['max_value_length'] = len( val )
			m['avg_key_length'] += len( nam )
			m['avg_value_length'] += len( val )
			m['total_size_bytes'] += sys.getsizeof( val ) + sys.getsizeof( nam )

		if m['num_of_tokens']:
			m['avg_key_length'] = round( m['avg_key_length'] / m['num_of_tokens'], 2 )
			m['avg_value_length'] = round( m['avg_value_length'] / m['num_of_tokens'], 2 )
		return m

def getstor( rootStor, name = '', mode = 0o755, gid = None, uid = None ):
	return Stor( rootStor = rootStor, name = name, mode = mode, gid = gid, uid = uid )

baseSectType = Stor
baseValType = SVal

def read_tokens_from_lines( lines, stok, addMode = defs.noOverwrite ):
	'''Loads tokens to 'stok' from a string list.'''
	'''Section names must be in square brackets and preceded by an empty line (if not at
	the beginning of the list) in order not to be mixed with tokens like [aaa]=[bbb].'''

	curPath = ''
	curTok = None

	emptyLine = True

	def flush():
		nonlocal curTok, curPath
		if curTok:
			mtok = reTok.match( curTok )
			if not mtok or mtok.lastindex != 2:
				raise IKException( ErrorCode.unknownDataFormat, l, "Cannot parse token." )
			stok.addToken( curPath + curTok, addMode )
			curTok = None

	for l in lines:
		if reEmpty.match( l ):
			emptyLine = True
			flush()
			continue

		m = reSect.match( l )
		if m and emptyLine:
			flush()
			curPath = m.group( 1 )
			if curPath[-1] != '/': curPath += "/"
			emptyLine = False
			continue

		emptyLine = False

		m = reMlTok.match( l )
		if m:
			if not curTok:
				raise IKException( ErrorCode.unknownDataFormat, l, "Cannot parse token." )
			curTok += stripOne( m.group( 1 ), False, True, '\n' )
			continue

		flush()
		curTok = stripOne( l, False, True, '\n' )
	flush()


def read_tokens_from_file( filename, tok, addMode = defs.noOverwrite ):
	'''Reads tokens from a text file. If the file contains several sections, their names
	should be specified in the file in square brackets in the beginning of each section.
	If tokens are loaded into a single section, its name can be specified either in the
	file or if the section name is not specified, tokens are loaded into the root section.
	'''
	if not os.path.exists( filename ):
		raise IKException( ErrorCode.objectNotExists, filename, "File with tokens is not found." )

	with open( filename ) as f:
		lines = f.readlines()

	read_tokens_from_lines( lines, tok, addMode )
