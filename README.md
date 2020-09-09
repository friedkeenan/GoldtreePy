# GoldtreePy
A python port of XorTroll's [Quark](https://github.com/XorTroll/Goldleaf/tree/master/Quark) (formerly named Goldtree). It is purely a CLI (no GUI).


To use, open Goldleaf, run Goldtree.py, and then open **Explore content -> Remote PC (via USB)** in Goldleaf.

```
usage: Goldtree.py [-h] [-f SELECTED_FILE] [drive_paths [drive_paths ...]]

positional arguments:
  drive_paths

optional arguments:
  -h, --help            show this help message and exit
  -f SELECTED_FILE, --selected-file SELECTED_FILE
```

Additionally, if you don't specify the `selected-file` argument and your first argument is a file, then that file will be treated as the selected file. The paths specified with the `drive_paths` arguments will show as drives in Goldleaf so you don't have to navigate a bunch of folders to get to them.

You need to run as root to run the script (using `sudo`) unless you set up udev rules to avoid this. This can be done with pheki's [nx-udev](https://github.com/pheki/nx-udev) project.

To install all the dependencies, do `pip3 install -r requirements.txt`. Requires a PyUSB backend such as `libusb`.
