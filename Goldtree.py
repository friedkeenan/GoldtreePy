#!/usr/bin/env python3

import usb.core
import usb.util
import struct
import sys
import os
import shutil
import psutil

def get_switch():
    dev = usb.core.find(idVendor=0x057e, idProduct=0x3000)
    if dev is None:
        raise ValueError("Device not found")
    return dev

def get_ep(dev):
    dev.set_configuration()
    intf=dev.get_active_configuration()[(0,0)]
    return (usb.util.find_descriptor(intf,
                custom_match=lambda e:usb.util.endpoint_direction(e.bEndpointAddress)==usb.util.ENDPOINT_OUT),
            usb.util.find_descriptor(intf,
                custom_match=lambda e:usb.util.endpoint_direction(e.bEndpointAddress)==usb.util.ENDPOINT_IN))

dev=get_switch()
ep=get_ep(dev)

def write(buffer, timeout=3000):
    ep[0].write(buffer, timeout=timeout)

def read(length, timeout=3000):
    return ep[1].read(length, timeout=timeout).tobytes()

def write_u32(x):
    write(struct.pack("<I", x))

def write_u64(x):
    write(struct.pack("<Q", x))

def write_string(x):
    write_u32(len(x))
    write(x.encode())

def read_u32():
    return struct.unpack("<I", read(4))[0]

def read_u64():
    return struct.unpack("<Q", read(8))[0]

def read_string():
    return read(read_u32() + 1)[:-1].decode()


class CommandId:
    ListSystemDrives = 0
    GetPathType = 1
    ListDirectories = 2
    ListFiles = 3
    GetFileSize = 4
    FileRead = 5
    FileWrite = 6
    CreateFile = 7
    CreateDirectory = 8
    DeleteFile = 9
    DeleteDirectory = 10
    RenameFile = 11
    RenameDirectory = 12
    GetDriveTotalSpace = 13
    GetDriveFreeSpace = 14
    GetNSPContents = 15

class Command:
    GUCI = b"GUCI"
    GUCO = b"GUCO"
    def __init__(self, cmd_id=0, out=True, raw=None):
        self.out = out
        if raw is None:
            self.cmd_id = cmd_id
            if out:
                self.magic = self.GUCO
            else:
                self.magic = self.GUCI
        else:
            self.magic = raw[:4]
            self.cmd_id = struct.unpack("<I", raw[4:])[0]
    def magic_ok(self):
        if self.out:
            return self.magic == self.GUCO
        else:
            return self.magic == self.GUCI
    def has_id(self,cmd_id):
        return self.cmd_id == cmd_id
    def write(self):
        write(self.magic)
        write_u32(self.cmd_id)
    @staticmethod
    def read():
        return Command(out=False, raw=read(4) + read(4))

drives = {}

def read_path():
    path = read_string()
    drive = path.split(":", 1)[0]
    path = path.replace(drive + ":", drives[drive])
    return path

def main():
    while True:
        while True:
            try:
                c = Command.read()
                break
            except usb.core.USBError:
                pass
        if c.has_id(CommandId.ListSystemDrives):
            if "win" not in sys.platform:
                drives["ROOT"] = "/"
            else:
                import string
                from ctypes import windll
                bitmask = windll.kernel32.GetLogicalDrives()
                for letter in string.ascii_uppercase:
                    if bitmask & 1:
                        print(letter)
                        drives[letter] = letter + ":"
                    bitmask >>= 1
            for d in sys.argv[1:]:
                folder = os.path.dirname(os.path.abspath(d))
                drives[os.path.basename(folder)] = folder
            print(drives)
            write_u32(len(drives))
            for d in drives:
                write_string(d)
                write_string(d)
        elif c.has_id(CommandId.GetPathType):
            ptype = 0
            path = read_path()
            if os.path.isfile(path):
                ptype = 1
            elif os.path.isdir(path):
                ptype = 2
            write_u32(ptype)
        elif c.has_id(CommandId.ListDirectories):
            path=read_path()
            ents=[x for x in os.listdir(path) if os.path.isdir(os.path.join(path, x))]
            write_u32(len(ents))
            for name in ents:
                write_string(name)
        elif c.has_id(CommandId.ListFiles):
            path=read_path()
            ents=[x for x in os.listdir(path) if os.path.isfile(os.path.join(path, x))]
            write_u32(len(ents))
            for name in ents:
                write_string(name)
        elif c.has_id(CommandId.GetFileSize):
            path = read_path()
            write_u64(os.path.getsize(path))
        elif c.has_id(CommandId.FileRead):
            offset = read_u64()
            size = read_u64()
            path = read_path()
            print(f"FileRead - Path: {path}, Offset: {offset}, Size: {size}")
            with open(path, "rb") as f:
                f.seek(offset)
                data = f.read(size)
            write_u64(len(data))
            print(f"FileRead - Read bytes: {len(data)}")
            write(data)
        elif c.has_id(CommandId.FileWrite):
            path = read_path()
            read_u32() # Hardcoded to zero
            offset = read_u32()
            size = read_u32()
            data = read(size)
            print(f"FileWrite - Path: ({path}), Offset: {offset}, Size: {size}")
            with open(path, "rwb") as f:
                cont=bytearray(f.read())
                cont[offset:offset + size] = data
                f.write(cont)
        elif c.has_id(CommandId.CreateFile):
            path = read_path()
            open(path, "a").close()
        elif c.has_id(CommandId.CreateDirectory):
            path = read_path()
            try:
                os.mkdir(path)
            except os.FileExistsError:
                pass
        elif c.has_id(CommandId.DeleteFile):
            path = read_path()
            os.remove(path)
        elif c.has_id(CommandId.DeleteDirectory):
            path = read_path()
            shutil.rmtree(path)
        elif c.has_id(CommandId.RenameFile) or c.has_id(CommandId.RenameDirectory):
            path = read_path()
            new_name = read_string()
            os.rename(path, new_name)
        elif c.has_id(CommandId.GetDriveTotalSpace):
            path = read_path()
            write_u64(psutil.disk_usage(path).total)
        elif c.has_id(CommandId.GetDriveFreeSpace):
            path = read_path()
            write_u64(psutil.disk_usage(path).free)

    return 0

if __name__ == "__main__":
    sys.exit(main())
