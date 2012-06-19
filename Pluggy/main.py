import os, sys, time, re, socket
from colorama import Fore, Back, Style
from Logger import Logger
from Config import Conf, Plugin, Aliases, CommandLineOptions
from ParserLog import RuleMatch

def cPrint(string, fore = Fore.WHITE, back = Back.RESET):
	print fore + back + string + Style.RESET_ALL 

class Pluggy:
	def createGroups(self, rule):
		event = {}
		for key, value in rule.rule.iteritems():
			if key not in ["regexp", "precheck"]:
				event[key] = self.plugin.get_replace_value(value.encode('utf-8'), rule.groups, rule._replace_assessment[key])

		return event

	def printGroup(self, event):
		for group in sorted(event):
			print Fore.MAGENTA + "\t%s" % group + Fore.WHITE + " -> " + Fore.CYAN + "%s" % event[group] + Style.RESET_ALL

	def main(self):
		self.plugin = Plugin()
		self.plugin.read([sys.argv[1]], "latin1")
		self.plugin.set('config', 'encoding', "latin1")
		if not self.plugin.get_validConfig():
			print("Error Loading!")

		plugin_rules = self.plugin.rules()
		rules = [RuleMatch(rule, plugin_rules[rule], self.plugin) for rule in sorted(plugin_rules)]

		cPrint("Loaded Rules:")

		for rule in rules: cPrint("\t%s" % rule.name, Fore.GREEN)
		rulesMatched = 0;
		ruleMatched = False

		for line in open(sys.argv[2]):
			for rule in rules:
				ruleTime = time.time()
				rule.feed(line)
				if rule.match() and not ruleMatched:
					ruleMatched = True
					ruleTime = (time.time() - ruleTime)
					cPrint("Found! Rule %s matches, Trigged on this line(s):" % rule.name)
					cPrint("\t%s" % rule.log, Fore.RED)
					cPrint("Matched Groups:")
					groups = self.createGroups(rule)				
					self.printGroup(groups)

					cPrint("Time Taken to run rule: %.3fsec" % ruleTime)



			if ruleMatched: 
				for rule in rules: rule.resetRule()
				ruleMatched = False
				rulesMatched += 1
		
		cPrint("Total Rules Matched: %d" % rulesMatched)








