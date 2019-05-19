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
    intf = dev.get_active_configuration()[(0,0)]
    return (usb.util.find_descriptor(intf,
                custom_match=lambda e:usb.util.endpoint_direction(e.bEndpointAddress)==usb.util.ENDPOINT_OUT),
            usb.util.find_descriptor(intf,
                custom_match=lambda e:usb.util.endpoint_direction(e.bEndpointAddress)==usb.util.ENDPOINT_IN))

dev = get_switch()
ep = get_ep(dev)

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

class CommandReadResult: # Currently not used in this code, but used in original Goldtree
    Success = 0
    InvalidMagic = 1
    InvalidCommandId = 2

class CommandId:
    ListSystemDrives = 0
    GetEnvironmentPaths = 1
    GetPathType = 2
    ListDirectories = 3
    ListFiles = 4
    GetFileSize = 5
    FileRead = 6
    FileWrite = 7
    CreateFile = 8
    CreateDirectory = 9
    DeleteFile = 10
    DeleteDirectory = 11
    RenameFile = 12
    RenameDirectory = 13
    GetDriveTotalSpace = 14
    GetDriveFreeSpace = 15
    GetNSPContents = 16
    Max = 17

class Command:

    GLUC = b"GLUC"

    def __init__(self, cmd_id=0, out=True):
        if out:
            self.cmd_id = cmd_id
            self.magic = self.GLUC
        else:
            while True:
                self.magic = read(4)
                if self.magic_ok():
                    break
            self.cmd_id = read_u32()

    def magic_ok(self):
        return self.magic == self.GLUC

    def has_id(self,cmd_id):
        return self.cmd_id == cmd_id

    def write(self):
        write(self.magic)
        write_u32(self.cmd_id)

    @staticmethod
    def read():
        return Command(out=False)

drives = {}

def read_path():
    path = read_string()
    drive = path.split(":", 1)[0]
    try:
        path = path.replace(drive + ":/", drives[drive])
    except KeyError:
        pass
    return path

def main():
    while True:
        while True:
            try:
                c = Command.read()
                break
            except usb.core.USBError:
                pass
            except KeyboardInterrupt:
                return 0
        if c.has_id(CommandId.ListSystemDrives):
            drive_labels = {}
            if "win" in value[:3].lower():
                import string
                import ctypes
                kernel32 = ctypes.windll.kernel32
                bitmask = kernel32.GetLogicalDrives()
                for letter in string.ascii_uppercase:
                    if bitmask & 1:
                        drives[letter] = letter + ":/"
                        label_buf = ctypes.create_unicode_buffer(1024)
                        kernel32.GetVolumeInformationW(
                            ctypes.c_wchar_p(letter + ":\\"),
                            label_buf,
                            ctypes.sizeof(label_buf),
                            None,
                            None,
                            None,
                            None,
                            0
                            )
                        if label_buf.value:
                            drive_labels[letter] = label_buf.value
                    bitmask >>= 1
            else:
                Goldleaf.drives["ROOT"] = "/"
            write_u32(len(drives))
            for d in drives:
                try:
                    write_string(drive_labels[d])
                except KeyError:
                    write_string(d)
                write_string(d)
        elif c.has_id(CommandId.GetEnvironmentPaths):
            env_paths = {x:os.path.expanduser("~/"+x) for x in ["Desktop", "Documents"]}

            for arg in sys.argv[1:]: # Add arguments as environment paths
                folder = os.path.abspath(arg)
                if os.path.isfile(folder):
                    folder = os.path.dirname(folder)
                env_paths[os.path.basename(folder)] = folder

            write_u32(len(env_paths))
            for env in env_paths:
                env_paths[env] = env_paths[env].replace("\\", "/")
                write_string(env)
                if env_paths[env][1:3] != ":/":
                    env_paths[env] = "ROOT:" + env_paths[env]
                write_string(env_paths[env])
        elif c.has_id(CommandId.GetPathType):
            ptype = 0
            path = read_path()
            if os.path.isfile(path):
                ptype = 1
            elif os.path.isdir(path):
                ptype = 2
            write_u32(ptype)
        elif c.has_id(CommandId.ListDirectories):
            path = read_path()
            ents = [x for x in os.listdir(path) if os.path.isdir(os.path.join(path, x))]
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
            with open(path, "rb") as f:
                f.seek(offset)
                data = f.read(size)
            write_u64(len(data))
            write(data)
        elif c.has_id(CommandId.FileWrite):
            offset = read_u64()
            size = read_u64()
            path = read_path()
            data = read(size)
            try:
                with open(path, "rb") as f:
                    cont = bytearray(f.read())
            except FileNotFoundError:
                cont = bytearray()
            cont[offset:offset + size] = data
            with open(path, "wb") as f:
                f.write(cont)
        elif c.has_id(CommandId.CreateFile):
            path = read_path()
            open(path, "a").close()
        elif c.has_id(CommandId.CreateDirectory):
            path = read_path()
            try:
                os.mkdir(path)
            except FileExistsError:
                pass
        elif c.has_id(CommandId.DeleteFile):
            path = read_path()
            os.remove(path)
        elif c.has_id(CommandId.DeleteDirectory):
            path = read_path()
            shutil.rmtree(path)
        elif c.has_id(CommandId.RenameFile):
            path = read_path()
            new_name = read_string()
            os.rename(path, f"{os.path.dirname(path)}/{new_name}")
        elif c.has_id(CommandId.RenameDirectory):
            path = read_path()
            new_name = read_path()
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
