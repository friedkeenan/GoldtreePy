# GoldtreePy
A python port of XorTroll's [Goldtree](https://github.com/XorTroll/Goldleaf/tree/master/Goldtree)


To use, open Goldleaf, do `sudo ./Goldtree.py [<path>...]` (`sudo` isn't required if you use [udev rules](https://github.com/pheki/nx-udev) or macos), and then open **Explore content -> Remote PC (via USB)** in Goldleaf. The arguments will show up as drives in Goldleaf so that you don't have to navigate to the folders/files from the root of your computer.

To install all the dependencies, do `pip3 install -r requirements.txt`. Requires a PyUSB backend such as `libusb`.
