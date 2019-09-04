#!/usr/bin/env python3

import usb.core
import usb.util
import struct
import sys
import os
import shutil
import io
from enum import Enum
from pathlib import Path
from collections import OrderedDict

class USBHandler:
    CommandBlockLength = 0x1000

    def __init__(self, idVendor=0x057e, idProduct=0x3000):
        super().__init__()

        self.dev = usb.core.find(idVendor=idVendor, idProduct=idProduct)
        if self.dev is None:
            raise ValueError("Device not found")

        self.ep = self.get_ep()

        self.read_buf = io.BytesIO()
        self.write_buf = io.BytesIO()

        self.drives = OrderedDict()

    def get_ep(self):
        self.dev.set_configuration()
        intf = self.dev.get_active_configuration()[(0,0)]
        return (usb.util.find_descriptor(intf,
                    custom_match=lambda e:usb.util.endpoint_direction(e.bEndpointAddress)==usb.util.ENDPOINT_OUT),
                usb.util.find_descriptor(intf,
                    custom_match=lambda e:usb.util.endpoint_direction(e.bEndpointAddress)==usb.util.ENDPOINT_IN))

    def clear(self):
        self.read_buf = io.BytesIO()
        self.write_buf = io.BytesIO()

    def read(self, size=-1):
        if size == str:
            length = self.read("I") * 2
            return self.read(length).decode("utf-16-le")

        elif size == Path:
            path = self.read(str)
            drive = path.split(":", 1)[0]
            try:
                path = path.replace(drive + ":", str(self.drives[drive][0])).replace("//", "/")
            except KeyError:
                pass
            return Path(path)

        elif isinstance(size, str):
            fmt = size
            size = struct.calcsize(fmt)

        else:
            fmt = None

        pos = self.read_buf.tell()
        ret = self.read_buf.read(size)

        if size == -1:
            pass
        elif self.read_buf.read(1) != b"":
            self.read_buf.seek(-1, 1)
            pass
        else:
            length = self.read_buf.tell()

            self.read_buf = io.BytesIO(self.read_raw(self.CommandBlockLength))

            ret += self.read_buf.read(size - length + pos)

        if fmt is not None:
            ret = struct.unpack(f"<{fmt}", ret)
            if len(ret) == 1:
                ret = ret[0]

        return ret

    def write(self, fmt, *args):
        if len(args) < 1:
            if isinstance(fmt, str):
                self.write("I", len(fmt))
                self.write(fmt.encode("utf-16-le"))

            elif isinstance(fmt, Path):
                path = str(fmt)
                for d,p in self.drives.items():
                    if path.startswith(str(p[0])):
                        path = f"{d}:/{path[len(str(p[0])):]}"
                        break
                self.write(path)

            else:
                self.write_buf.write(fmt)

        else:
            self.write(struct.pack(f"<{fmt}", *args))

    def send(self):
        self.write(b"\x00" * (self.CommandBlockLength - self.write_buf.tell()))
        self.write_raw(self.write_buf.getbuffer())

    def read_raw(self, size, timeout=3000):
        return self.ep[1].read(size, timeout=timeout).tobytes()

    def write_raw(self, data, timeout=3000):
        self.ep[0].write(data, timeout=timeout)

    def add_drive(self, drive, path, label=None):
        if label is None:
            label = drive
        self.drives[drive] = (Path(path), label)

    def get_drive(self, idx):
        return list(self.drives.items())[idx]

def make_result(module, description):
    return ((((module)&0x1FF)) | ((description)&0x1FFF)<<9)

class CommandId(Enum):
    GetDriveCount = 0
    GetDriveInfo = 1
    StatPath = 2
    GetFileCount = 3
    GetFile = 4
    GetDirectoryCount = 5
    GetDirectory = 6
    ReadFile = 7
    WriteFile = 8
    Create = 9
    Delete = 10
    Rename = 11
    GetSpecialPathCount = 12
    GetSpecialPath = 13
    SelectFile = 14
    Max = 15

class Command:
    InputMagic = b"GLCI"
    OutputMagic = b"GLCO"

    ResultModule = 356 # Goldleaf result module

    ResultSuccess = 0
    ResultInvalidInput = make_result(ResultModule, 101)

    handler = USBHandler()

    def __init__(self, cmd_id=CommandId.GetDriveCount, out=True):
        if out:
            self.cmd_id = cmd_id
            self.magic = self.OutputMagic
        else:
            magic = self.read(4)
            if magic != self.InputMagic:
                raise ValueError(f"Input magic mismatch")

            self.cmd_id = CommandId(self.read("I"))

    def has_id(self, cmd_id):
        return self.cmd_id == cmd_id

    def read(self, *args, **kwargs):
        return self.handler.read(*args, **kwargs)

    def write(self, *args, **kwargs):
        self.handler.write(*args, **kwargs)

    def read_raw(self, size):
        return self.handler.read_raw(size)

    def write_raw(self, buf):
        self.handler.write_raw(buf)

    def write_base(self, result=ResultSuccess):
        self.write(self.OutputMagic)
        self.write("I", result)

    def send(self):
        self.handler.send()

    @classmethod
    def read_cmd(cls):
        cls.handler.clear()
        return cls(out=False)

    def __repr__(self):
        return f"Command({self.cmd_id})"

def main():
    special_paths = {x: Path("~", x).expanduser() for x in ["Desktop", "Documents"]}
    special_paths = OrderedDict({x: y for x,y in special_paths.items() if y.exists()})

    write_file = io.IOBase()
    write_file.close()

    read_file = io.IOBase()
    read_file.close()

    while True:
        while True:
            try:
                c = Command.read_cmd()
                print(f"Received command: {c.cmd_id}")
                break
            except usb.core.USBError:
                pass
            except KeyboardInterrupt:
                return 0

        if not c.has_id(CommandId.WriteFile):
            write_file.close()
        if not c.has_id(CommandId.ReadFile):
            read_file.close()

        bufs = []

        if c.has_id(CommandId.GetDriveCount):
            c.handler.add_drive("ROOT", "/")

            for arg in sys.argv[1:]: # Add arguments as drives
                folder = Path(arg).absolute()
                if folder.is_file():
                    folder = folder.parent
                c.handler.add_drive(folder.name, folder)

            c.write_base()
            c.write("I", len(c.handler.drives))

        elif c.has_id(CommandId.GetDriveInfo):
            drive_idx = c.read("I")

            if drive_idx >= len(c.handler.drives):
                c.write_base(Command.ResultInvalidInput)

            else:
                info = c.handler.get_drive(drive_idx)

                c.write_base()

                c.write(info[1][1]) # Label
                c.write(info[0]) # Prefix

                #usage = shutil.disk_usage(info[1][0])
                #c.write("II", usage.free & 0xFFFFFFFF, usage.total & 0xFFFFFFFF) # Not used by Goldleaf but still sent

                c.write("II", 0, 0) # Stubbed free/total space (not used by Goldleaf)

        elif c.has_id(CommandId.StatPath):
            path = c.read(Path)
            type = 0
            file_size = 0

            if path.is_file():
                type = 1
                file_size = path.stat().st_size
            elif path.is_dir():
                type = 2
            else:
                c.write_base(Command.ResultInvalidInput)
                c.send()
                continue

            c.write_base()
            c.write("IQ", type, file_size)

        elif c.has_id(CommandId.GetFileCount):
            path = c.read(Path)

            if path.is_dir():
                files = [x for x in path.glob("*") if x.is_file()]
                c.write_base()
                c.write("I", len(files))

            else:
                c.write_base(Command.ResultInvalidInput)

        elif c.has_id(CommandId.GetFile):
            path = c.read(Path)
            file_idx = c.read("I")

            if path.is_dir():
                files = [x for x in path.glob("*") if x.is_file()]

                if file_idx >= len(files):
                    c.write_base(Command.ResultInvalidInput)

                else:
                    c.write_base()
                    c.write(files[file_idx].name)

            else:
                c.write_base(Command.ResultInvalidInput)

        elif c.has_id(CommandId.GetDirectoryCount):
            path = c.read(Path)

            if path.is_dir():
                dirs = [x for x in path.glob("*") if x.is_dir()]
                c.write_base()
                c.write("I", len(dirs))

            else:
                c.write_base(Command.ResultInvalidInput)

        elif c.has_id(CommandId.GetDirectory):
            path = c.read(Path)
            dir_idx = c.read("I")

            if path.is_dir():
                dirs = [x for x in path.glob("*") if x.is_dir()]

                if dir_idx >= len(dirs):
                    c.write_base(Command.ResultInvalidInput)

                else:
                    c.write_base()
                    c.write(dirs[dir_idx].name)

            else:
                c.write_base(Command.ResultInvalidInput)

        elif c.has_id(CommandId.ReadFile):
            path = c.read(Path)
            offset, size = c.read("QQ")

            try:
                if read_file.closed:
                    read_file = path.open("rb")

                read_file.seek(offset)
                bufs.append(read_file.read(size))

                c.write_base()
                c.write("Q", size)

            except:
                c.write_base(Command.ResultInvalidInput)

        elif c.has_id(CommandId.WriteFile):
            path = c.read(Path)
            size = c.read("Q")
            data = c.read_raw(size)

            try:
                if write_file.closed:
                    write_file = path.open("wb")

                write_file.write(data)

                c.write_base()

            except:
                c.write_base(Command.ResultInvalidInput)

        elif c.has_id(CommandId.Create):
            type = c.read("I")
            path = c.read(Path)

            if type == 1:
                try:
                    path.touch()
                    c.write_base()

                except:
                    c.write_base(Command.ResultInvalidInput)

            elif type == 2:
                try:
                    path.mkdir()
                    c.write_base()

                except:
                    c.write_base(Command.ResultInvalidInput)

            else:
                c.write_base(Command.ResultInvalidInput)

        elif c.has_id(CommandId.Delete):
            type = c.read("I")
            path = c.read(Path)

            try:
                if type == 1:
                    os.remove(path)
                    c.write_base()

                elif type == 2:
                    shutil.rmtree(path)
                    c.write_base()

                else:
                    c.write_base(Command.ResultInvalidInput)
            except:
                c.write_base(Command.ResultInvalidInput)

        elif c.has_id(CommandId.Rename):
            type = c.read("I")
            path = c.read(Path)
            new_path = c.read(Path)

            try:
                path.rename(new_path)
                c.write_base()

            except:
                c.write_base(Command.ResultInvalidInput)

        elif c.has_id(CommandId.GetSpecialPathCount):
            c.write_base()
            c.write("I", len(special_paths))

        elif c.has_id(CommandId.GetSpecialPath):
            spath_idx = c.read("I")

            if spath_idx >= len(special_paths):
                c.write_base(Command.ResultInvalidInput)

            else:
                info = list(special_paths.items())[spath_idx]

                c.write_base()
                c.write(info[0])
                c.write(info[1])

        elif c.has_id(CommandId.SelectFile): # Never used
            try:
                print()
                path = Path(input("Select file for Goldleaf: ")).absolute()
                c.write_base()
                c.write(path)

            except:
                c.write_base(Command.ResultInvalidInput)

        c.send()

        for buf in bufs:
            c.write_raw(buf)

    return 0

if __name__ == "__main__":
    sys.exit(main())
