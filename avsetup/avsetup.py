#!/usr/bin/env python

import curses
import curses.textpad
import re
import shutil
import os
import signal
import sys

class avsetup:
	def __init__(self):
		self.screen = curses.initscr()
		curses.start_color()
		curses.noecho()
		curses.cbreak()
		curses.curs_set(0)
		self.screen.keypad(1)
		curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_GREEN)
		curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
		curses.init_pair(3, curses.COLOR_BLACK, curses.COLOR_RED)

		self.screen.bkgd(' ', curses.color_pair(1))
		self.settings = {}

		self.regexIP = "^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$";
		self.regexHostName = "^([a-z0-9]+(-[a-z0-9]+)*\.)+[a-z]{2,}$";


	def printOptions(self):
		options = [( "Welcome to OSSIM! by Alien Vault", True), ("Please Select an Option...", True),
			     ("1 - Set Nameserver", True),("2 - Setup Network", False),("3 - Setup OSSIM IPs", False),("4 - Update OSSIM  ", False),("Q - Quit", False)];

		self.screen.clear()
		self.screen.border(0)
		self.size = self.screen.getmaxyx()

		currentPos = 2
		defaultSize = 1
		for option in options:
			defaultSize = (self.size[1] - len(option[0])) / 2 if option[1] is True else defaultSize
			self.screen.addstr(currentPos, defaultSize, option[0])
			currentPos += 1


		self.printSettings()	
		self.screen.refresh()

	def printSettings(self):
		currentPos = (self.size[0] - (len(self.settings) +2))
		for key in sorted(self.settings):
			self.screen.addstr(currentPos, 2, key + ":" + self.settings[key])
			currentPos +=1


	def dialogBox(self, string, color):

		positionY = (self.size[0] - 3) / 2
		positionX = (self.size[1] - (len(string) + 6)) / 2

		frame = curses.newwin(3, len(string) + 6, positionY, positionX)

		frame.clear()
		frame.border()
		frame.bkgd(' ', curses.color_pair(color))
		frame.addstr(1,3, string)
		frame.refresh()

		self.screen.refresh()
		retValue = self.screen.getch()

		del frame

		return retValue


	def printError(self, string):
		self.dialogBox(string, 3)
		self.printOptions()

	def writeOptions(self):
		ret = self.dialogBox("Write Values? Y - Yes, N - No", 3)

		if ord('y') == ret or ord('Y') == ret:
			return True

		return False

	def checkIP(self, ip):
		if not re.match(self.regexIP, ip.strip()):
			return False
		return ip

	def getIP(self, name):
		ip = self.checkIP(self.makeTextBox(10, 10, "Please Enter " + name, 2, 2))
		while not ip:
			self.printError("Invalid " + name + "!")
			ip = self.checkIP(self.makeTextBox(10, 10, "Please Enter " + name, 2, 2))

		return ip

	def getString(self, name):
		return self.makeTextBox(10, 10, "Please Enter " + name, 2, 2)

			


	def updateInterface(self):

		if os.path.isfile("/etc/network/interfaces"):
			try:
				shutil.move("/etc/network/interfaces", "/etc/network/interfaces_backup")
			except IOError:

				dialogBox("Unable to write to interfaces! Are you root?")
				return False
			
		interfaces = open("/etc/network/interfaces", "w")
		interfaces.write("auto lo\n")
		interfaces.write("iface lo inet loopback\n\n")
		interfaces.write("allow-hotplug eth0\n")
		interfaces.write("iface eth0 inet static\n")
		interfaces.write("\taddress " + self.settings["IP Address"])
		interfaces.write("\n\tnetmask " + self.settings["Netmask"])
		interfaces.write("\n\tnetwork " + self.settings["Network Address"])
		interfaces.write("\n\tbroadcast " + self.settings["Broadcast Address"])
		interfaces.write("\n\tgateway " + self.settings["Gateway Address"])
		interfaces.write("\n")

		interfaces.flush()
		interfaces.close()

		return True

	def updateNameserver(self):
		if os.path.isfile("/etc/resolv.conf"):
			try:
				shutil.move("/etc/resolv.conf", "/etc/resolv.conf_backup")
			except IOError:
				dialogBox("Unable to write resolv.conf! Are you root?")
				return False

		conf = open("/etc/resolv.conf", "w")
		conf.write("domain " + self.settings["Host Domain"])
		conf.write("\nsearch " + self.settings["Search Domain"])
		conf.write("\nnameserver " + self.settings["Name Server"])
		conf.write("\n")

		conf.flush()
		conf.close()

		return True
	
	def updateOssimSetup(self):
		if os.path.isfile("/etc/ossim/ossim_setup.conf"):
			try:
				shutil.move("/etc/ossim/ossim_setup.conf", "/etc/ossim/ossim_setup.conf_backup")
			except IOError:
				dialogBox("Unable to write to ossim_setup.conf! Are you root?")
				return False

			setup = open("/etc/ossim/ossim_setup.conf", "w")

			for line in open("/etc/ossim/ossim_setup.conf_backup", "r"):
				if "framework_ip=" in line:
					setup.write("framework_ip=" + self.settings["OSSIM Framework IP"] + "\n")
				elif "admin_ip=" in line:
					setup.write("admin_ip=" + self.settings["OSSIM Admin IP"] + "\n")
				else:
					setup.write(line)

			setup.flush()
			setup.close()

			return True

		else:
			self.dialogBox("Error! No ossim_setup.conf found!", 3)
			return False



	def main(self):

		self.printOptions()
		while True:
			input = self.screen.getch()
			if input == ord("Q") or input == ord('q'):
				break
			elif input == ord('1'):
				self.settings["Name Server"] = self.getIP("Name Server")
				self.settings["Host Domain"] = self.getString("Host Domain")
				self.settings["Search Domain"] = self.getString("Search Domain")

				if not self.writeOptions() or not self.updateNameserver():
					del self.settings["Name Server"] 
					del self.settings["Host Domain"]
					del self.settings["Search Domain"]

			elif input == ord('2'):
				self.settings["IP Address"] = self.getIP("IP Address")
				self.settings["Network Address"]  = self.getIP("Network Address")
				self.settings["Netmask"] = self.getIP("Netmask")
				self.settings["Broadcast Address"] = self.getIP("Broadcast Address") 
				self.settings["Gateway Address"] = self.getIP("Gateway Address")

				if not self.writeOptions() or not self.updateInterface():
					del self.settings["IP Address"] 
					del self.settings["Network Address"]  
					del self.settings["Netmask"] 
					del self.settings["Broadcast Address"] 
					del self.settings["Gateway Address"]

			
			elif input == ord('3'):
				self.settings["OSSIM Admin IP"] = self.getIP("OSSIM Admin IP")
				self.settings["OSSIM Framework IP"]= self.getIP("OSSIM Framework IP")

				if not self.writeOptions() or not self.updateOssimSetup():
					del self.settings["OSSIM Admin IP"] 
					del self.settings["OSSIM Framework IP"]

			elif input == ord('4'):
				os.system("ossim-update -u")



			self.printOptions()

	def makeTextBox(self, y, x, query="", textColor=0, frameColor=0):
		frame = curses.newwin(5, self.size[1] - (6 + x) , y, x)
		frame.clear()
		frame.border()
		frame.bkgd(' ', curses.color_pair(frameColor))
		frame.addstr(0,2, query)
		frame.refresh()
		textFrame = curses.newwin(1, self.size[1] - (16 + x), y+2, x+5)

		textBox = curses.textpad.Textbox(textFrame)

		self.screen.refresh()
		
		output = textBox.edit()
		del frame

		return output 

	def nuke(self):
		curses.nocbreak()
		self.screen.keypad(0)
		curses.echo()
		curses.endwin()

def signal_handler(signal, frame):
	cursed.nuke()
	sys.exit(0)

def check_root():
	if not os.geteuid() == 0:
		sys.exit("This script must be run as root!")

if __name__ == "__main__":
	check_root()
	cursed= avsetup()
	signal.signal(signal.SIGINT, signal_handler) 
	cursed.main()
	cursed.nuke()
