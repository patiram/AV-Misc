#!/usr/bin/env python

import sys

if __name__ == "__main__":
	sys.path.append('agent/')
	from main import Pluggy 
	pluggy = Pluggy()
	pluggy.main()

