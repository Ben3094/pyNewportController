from aenum import MultiValueEnum
from serial import Serial, SerialTimeoutException
from time import sleep
from threading import Lock, Thread

ADDRESS_RANGE = range(32)

class ControllerState(MultiValueEnum):
	NotReferenced = '0A', '0B', '0C', '0D', '0E', '0F', '10', '11'
	Configuration = '14'
	Homing = '1E', '1F'
	Moving = '28'
	Ready = '32', '33', '34', '35'
	Disable = '3C', '3D', '3E'
	Jogging = '46', '47'
	Unknown = 'Unknown'

class Controller():
	def __init__(self, mainController, address=1):
		"""
		:param mainController: The main controller connected to the computer 
		:type controller: :class:`MainController`
		:param address: the address of the new controller
		:type axis: int
		"""
		self._mainController = mainController
		if address in ADDRESS_RANGE:
			self._address = address

		self.Read = self.MainController.Read   
		self.read_error = self.MainController.read_error

		self.IsConnected = False

	def __getNone__():
		return None

	@property
	def Address(self):
		return self._address

	@property
	def MainController(self):
		return self._mainController

	def Connect(self, homeIsHardwareDefined:bool=True, wait:bool=True):
		self.IsConnected = True
		try:
			# self.UpdateStageSettings() # Too long to execute
			self.HomeIsHardwareDefined = homeIsHardwareDefined
			self.SetState(ControllerState.Ready, wait=wait)
			return True
		except Exception as e:
			self.IsConnected = False

	def Disconnect(self):
		self.IsConnected = False

	def Write(self, string):
		self.MainController.SuperWrite((str(self._address) if self._address is not None else "") + string)
	
	def Query(self, string, check_error=False):
		query = (str(self._address) if self._address is not None else "") + string
		reply = self.MainController.SuperQuery(query + '?', check_error)
		return reply[len(query):]
	
	@property
	def id(self):
		"""The axis model and serial number."""
		return self.Query('ID')

	@property
	def IsEnabled(self) -> bool:
		return self.Query('MM') == 1
	@IsEnabled.setter
	def IsEnabled(self, value:bool):
		self.Write('MM' + str(int(bool(value))))

	@property
	def HomeIsHardwareDefined(self) -> bool:
		match self.Query('HT'):
			case '1': return False
			case '2': return True
			case _:
				sleep(0.1)
				return self.HomeIsHardwareDefined
	@HomeIsHardwareDefined.setter
	def HomeIsHardwareDefined(self, value:bool):
		value = bool(value)
		if value != self.HomeIsHardwareDefined:
			self.State = ControllerState.Configuration
			self.Write('HT' + ('2' if value else '1'))

	def GoHome(self, wait=True):
		self.Write('OR')
		if wait:
			while(self.State == ControllerState.Moving):
				sleep(0.1)

	def GoTo(self, position, wait=True):
		self.Position = position
		if wait:
			while(self.State == ControllerState.Moving):
				sleep(0.1)

	@property
	def Position(self):
		"""The TP command returns the value of the current position.
			This is the position where the positioner actually is according to his encoder value.
			In MOVING state, this value always changes.
			In READY state, this value should be equal or very close to the set point and target position.
			Together with the TS command, the TP command helps evaluating whether a motion is completed"""
		return float(self.Query('TP'))
	@Position.setter
	def Position(self, value):
		if self.MinPosition <= value <= self.MaxPosition:
			self.Write('PA' + str(float(value)))
		else:
			raise Exception('Position cannot be reached')

	@property
	def MinPosition(self) -> float:
		return float(self.Query('SL'))

	@property
	def MaxPosition(self) -> float:
		return float(self.Query('SR'))

	def Stop(self):
		"""The ST command is a safety feature. It stops a move in progress by decelerating the positioner immediately with the acceleration defined by the AC command until it stops."""
		self.Write('ST')

	def GetState(self) -> ControllerState:
		try:
			self.MainController.__stateLock__.acquire()
			state = self.Query('TS')[-2:]
			state = ControllerState(state)
			self.MainController.__stateLock__.release()
			return state
		except:
			return ControllerState.Unknown
	def __setState__(self, value:ControllerState):
		while self.State == ControllerState.Unknown:
			sleep(0.1)
			
		match ControllerState(value):
			case ControllerState.NotReferenced:
				self.Reset()

			case ControllerState.Configuration:
				self.State = ControllerState.NotReferenced
				self.Write('PW1')

			case ControllerState.Ready:
				if self.State == ControllerState.Configuration:
					self.Write('PW0')
				if self.State == ControllerState.NotReferenced:
					self.GoHome()
				if self.State == ControllerState.Disable:
					self.Write('MM1')
				if (self.State == ControllerState.Jogging) or (self.State == ControllerState.Moving) or (self.State == ControllerState.Homing):
					sleep(0.3)

			case ControllerState.Disable:
				self.Write('MM0')
		
		if self.State != value:
			self.State = value
		
	def SetState(self, value:ControllerState, wait: bool= True):
		thread = Thread(target=self.__setState__, args=[value])
		thread.start()
		if wait:
			thread.join()				
	State = property(GetState, SetState)
					
	@property
	def Velocity(self) -> float:
		return float(self.Query('VA'))

	@property
	def Version(self) -> str:
		"""Get controller revision information"""
		return self.Query('VE')

	@property
	def Stage(self):
		""""Get the current connected stage reference"""
		return self.Query('ZX')
	
	def SetAutoStageCheck(self, value):
		return self.Write('ZX' + ('3' if bool(value) else '1'))
	
	def UpdateStageSettings(self):
		return self.Query('ZX2')
	
	def Reset(self):
		savedTimeout = self.MainController.__serialPort__.timeout
		self.MainController.__serialPort__.timeout = 0.1
		self.Write('RS')
		sleep(0.5)
		while self.State is not ControllerState.NotReferenced:
			sleep(0.1)
		self.MainController.__serialPort__.timeout = savedTimeout

class MainController(Controller):
	__stateLock__ = Lock()

	def __init__(self, address=1):
		super().__init__(self, address)
		self._slaveControllers = list()

	def Connect(self, port, homeIsHardwareDefined:bool=True, wait:bool=True):
		""":param port: Serial port connected to the main controller."""
		if not self.IsConnected:
			self.__serialPort__ = Serial(port=port, baudrate=56700, timeout=1)
			self.__serialPort__.setDTR(False)
			super().Connect(homeIsHardwareDefined=homeIsHardwareDefined, wait=wait)

	def Disconnect(self):
		if self.IsConnected:
			super().Disconnect()
			self.__serialPort__.close()
	
	@property
	def IsAllConnected(self):
		for controller in self.SlaveControllers:
			if not controller.IsConnected:
				return False
		return True

	def __del__(self):
		self.__serialPort__.close()

	def Read(self):
		str = self.__serialPort__.readline()
		str = str.replace(b'\r', b'')
		str = str.replace(b'\n', b'')
		return str

	def SuperWrite(self, value):
		try:
			return self.__serialPort__.write((value + '\r\n').encode(encoding='ascii'))
		except SerialTimeoutException:
			sleep(0.2)
			self.SuperWrite(value)

	def SuperQuery(self, value, check_error=False):
		with Lock():
			if check_error:
				self.raise_error()
			self.SuperWrite(value)
			if check_error:
				self.raise_error()
			return self.Read().decode(errors='replace')

	def Abort(self):
		"""The ST command is a safety feature. It stops a move in progress by decelerating the positioner immediately with the acceleration defined by the AC command until it stops."""
		self.SuperWrite('ST')

	def read_error(self):
		"""Return the last error as a string."""
		return self.SuperQuery('TB')
		
	def raise_error(self):
		"""Check the last error message and raise a NewportError."""
		err = self.read_error()
		if err[0] != "0":
			raise Exception(err)

	@property
	def SlaveControllers(self):
		return self._slaveControllers
	
	def NewController(self, address=1):
		newController = Controller(self, address=address)
		self._slaveControllers.append(newController)
		return newController