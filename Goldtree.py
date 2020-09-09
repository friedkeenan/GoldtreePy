#!/usr/bin/env python3

import usb.core
import usb.util
import enum
import io
import shutil
from ctypes import *
from pathlib import Path

class UsbInterface:
    default_timeout = 3000

    def __init__(self, vendor_id=0x057e, product_id=0x3000):
        self.dev = usb.core.find(idVendor=vendor_id, idProduct=product_id)
        if self.dev is None:
            raise ValueError("Device not found")

        self.dev.set_configuration()
        intf = self.dev.get_active_configuration()[(0, 0)]

        self.ep_out = usb.util.find_descriptor(intf,
            custom_match = lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT,
        )

        self.ep_in = usb.util.find_descriptor(intf,
            custom_match = lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN,
        )

    def read(self, size, *, timeout=default_timeout):
        while True:
            try:
                return self.ep_in.read(size,
                    timeout = timeout if timeout is not None else self.default_timeout,
                ).tobytes()
            except usb.core.USBError as e:
                if timeout is not None:
                    raise e

    def write(self, data, *, timeout=default_timeout):
        while True:
            try:
                self.ep_out.write(data,
                    timeout = timeout if timeout is not None else self.default_timeout,
                )
                return
            except usb.core.USBError as e:
                if timeout is not None:
                    raise e

class CommandId(enum.Enum):
    Invalid             = 0
    GetDriveCount       = 1
    GetDriveInfo        = 2
    StatPath            = 3
    GetFileCount        = 4
    GetFile             = 5
    GetDirectoryCount   = 6
    GetDirectory        = 7
    StartFile           = 8
    ReadFile            = 9
    WriteFile           = 10
    EndFile             = 11
    Create              = 12
    Delete              = 13
    Rename              = 14
    GetSpecialPathCount = 15
    GetSpecialPath      = 16
    SelectFile          = 17


def command_handler(id):
    """
    Registers the decorated function as
    a command handler.

    The annotations for arguments will be
    read and passed to the decorated function.

    Return None or 0 for a success, return
    a non-zero int for a failure.
    """

    def dec(func):
        def wrapper(self):
            return func(self, **{x: self.read(y) for x, y in func.__annotations__.items()})

        # Set the _handle_id attribute to be later
        # recognized and registered by the class
        wrapper._handle_id = id

        return wrapper

    return dec

class CommandProcessor:
    block_size = 0x1000

    in_magic  = b"GLCI"
    out_magic = b"GLCO"

    def __init__(self, drive_paths=(), *, selected_path=None):
        self.selected_path = selected_path

        self.drives = {}
        for path in drive_paths:
            path = path.absolute()
            if path.is_file():
                path = path.parent

            self.drives[path.name] = path

        self.drives["ROOT"] = Path("/")

        self.special_paths = [Path("~", x).expanduser() for x in ("Desktop", "Documents")]
        self.special_paths = [x for x in self.special_paths if x.exists()]

        self.command_handlers = {}
        for attr in dir(self):
            tmp = getattr(self, attr)

            if hasattr(tmp, "_handle_id"):
                # If the function was decorated with
                # the command_handler function, then
                # it will have the _handle_id attribute
                self.command_handlers[tmp._handle_id] = tmp

    def reset_buffers(self):
        self.read_buf = io.BytesIO()
        self.write_buf = io.BytesIO()
        self.buffers.clear()

    def read(self, *args):
        ret = []

        for arg in args:
            if isinstance(arg, type):
                if arg == str:
                    size = self.read(c_uint32)
                    tmp = self.read_buf.read(size * 2)
                    ret.append(tmp.decode("utf-16-le"))

                elif arg == Path:
                    path = self.read(str)
                    drive = path.split(":", 1)[0]

                    try:
                        drive_path = self.drives[drive]
                    except KeyError:
                        raise ValueError(f"Drive not found: {drive}")

                    path = str(drive_path) + path[len(f"{drive}:"):]
                    path = path.replace("//", "/")

                    ret.append(Path(path))

                else:
                    if issubclass(arg, Array):
                        arg = arg._type_.__ctype_le__ * arg._length
                    elif not issubclass(arg, Structure):
                        arg = arg.__ctype_le__

                    tmp = arg()
                    self.read_buf.readinto(tmp)
                    ret.append(tmp.value)
            else:
                ret.append(self.read_buf.read(arg))

        if len(ret) == 1:
            return ret[0]

        if len(ret) == 0:
            return None

        return tuple(ret)

    def write(self, *args):
        for arg in args:
            if isinstance(arg, str):
                self.write(c_uint32(len(arg)))
                self.write(arg.encode("utf-16-le"))
            elif isinstance(arg, Path):
                arg = arg.absolute()

                for drive, path in self.drives.items():
                    if path in arg.parents:
                        arg = arg.relative_to(path)
                        arg = f"{drive}:/{arg}"
                        break
                else:
                    raise ValueError("Somehow you have a path with no valid drive")

                self.write(arg)
            else:
                self.write_buf.write(arg)

    def add_buffer(self, buf):
        self.buffers.append(buf)

    def get_buffer(self, size):
        return self.usb.read(size)

    def start(self):
        self.usb = UsbInterface()
        print(f"Connected to {self.usb.dev.product} - {self.usb.dev.serial_number}")

        self.read_buf = io.BytesIO()
        self.write_buf = io.BytesIO()

        self.read_file = io.IOBase()
        self.write_file = io.IOBase()
        self.read_file.close()
        self.write_file.close()

        self.buffers = []

    def send(self, res):
        resp_buf = io.BytesIO()
        resp_buf.write(self.out_magic)
        resp_buf.write(c_uint32(res))

        self.write_buf.seek(0)
        resp_buf.write(self.write_buf.read())

        resp_buf.write(b"\x00" * (self.block_size - resp_buf.tell()))

        resp_buf.seek(0)
        self.usb.write(resp_buf.read())

        for buf in self.buffers:
            self.usb.write(buf)

        self.reset_buffers()

    def loop(self):
        self.start()

        while True:
            while True:
                try:
                    self.read_buf.write(self.usb.read(self.block_size, timeout=None))
                    self.read_buf.seek(0)
                    break
                except KeyboardInterrupt:
                    return

            magic = self.read(4)
            if magic != self.in_magic:
                print(f"Invalid input magic: {magic}")
                print(self.read_buf.getvalue())
                continue

            id = CommandId(self.read(c_uint32))
            print(f"Command: {id.name}")

            if id not in self.command_handlers:
                print(f"Unhandled command: {id.name}")
                continue

            res = self.command_handlers[id]()
            if res is None:
                res = 0

            if res != 0:
                print(f"An error occured: {res:#x}")

            self.send(res)

    @command_handler(CommandId.GetDriveCount)
    def get_drive_count(self):
        self.write(c_uint32(len(self.drives)))

    @command_handler(CommandId.GetDriveInfo)
    def get_drive_info(self, idx: c_uint32):
        if idx < len(self.drives):
            drive = tuple(self.drives.keys())[idx]

            # Label and prefix
            self.write(drive, drive)

            # Formerly free and total space, now no longer used
            self.write(c_uint32(0), c_uint32(0))
        else:
            return 0xDEAD

    @command_handler(CommandId.StatPath)
    def stat_path(self, path: Path):
        type = 0
        size = 0

        try:
            if path.is_file():
                type = 1
                size = path.stat().st_size
            elif path.is_dir():
                type = 2
            else:
                return 0xDEAD

            self.write(c_uint32(type), c_uint64(size))
        except:
            return 0xDEAD

    @command_handler(CommandId.GetFileCount)
    def get_file_count(self, path: Path):
        count = 0
        if path.is_dir():
            count = len([x for x in path.iterdir() if x.is_file()])

        self.write(c_uint32(count))

    @command_handler(CommandId.GetFile)
    def get_file(self, path: Path, idx: c_uint32):
        if not path.is_dir():
            return 0xDEAD

        files = [x for x in path.iterdir() if x.is_file()]
        if idx < len(files):
            self.write(files[idx].name)
        else:
            return 0xDEAD

    @command_handler(CommandId.GetDirectoryCount)
    def get_directory_count(self, path: Path):
        count = 0
        if path.is_dir():
            count = len([x for x in path.iterdir() if x.is_dir()])

        self.write(c_uint32(count))

    @command_handler(CommandId.GetDirectory)
    def get_directory(self, path: Path, idx: c_uint32):
        if not path.is_dir():
            return 0xDEAD

        dirs = [x for x in path.iterdir() if x.is_dir()]
        if idx < len(dirs):
            self.write(dirs[idx].name)
        else:
            return 0xDEAD

    @command_handler(CommandId.StartFile)
    def start_file(self, path: Path, mode: c_uint32):
        if mode == 1:
            self.read_file.close()
            self.read_file = path.open("rb")
        else:
            self.write_file.close()
            self.write_file = path.open("wb")

            if mode == 3:
                self.write_file.seek(0, 2)

    @command_handler(CommandId.ReadFile)
    def read_file(self, path: Path, offset: c_uint64, size: c_uint64):
        try:
            if not self.read_file.closed:
                self.read_file.seek(offset)
                buf = self.read_file.read(size)

                self.write(c_uint64(len(buf)))
                self.add_buffer(buf)
            else:
                with path.open("rb") as f:
                    f.seek(offset)
                    buf = f.read(size)

                self.write(c_uint64(len(buf)))
                self.add_buffer(buf)
        except:
            return 0xDEAD

    @command_handler(CommandId.WriteFile)
    def write_file(self, path: Path, size: c_uint64):
        buf = self.get_buffer(size)

        try:
            if not self.write_file.closed:
                self.write_file.write(buf)
            else:
                with path.open("wb") as f:
                    f.write(buf)
        except:
            return 0xDEAD

    @command_handler(CommandId.EndFile)
    def end_file(self, mode: c_uint32):
        if mode == 1:
            self.read_file.close()
        else:
            self.write_file.close()

    @command_handler(CommandId.Create)
    def create(self, type: c_uint32, path: Path):
        try:
            if type == 1:
                path.touch()
            elif type == 2:
                path.mkdir()
        except:
            return 0xDEAD

    @command_handler(CommandId.Delete)
    def delete(self, type: c_uint32, path: Path):
        try:
            if type == 1:
                path.unlink()
            elif type == 2:
                shutil.rmtree(path)
        except:
            return 0xDEAD

    @command_handler(CommandId.Rename)
    def rename(self, type: c_uint32, path: Path, new_path: Path):
        print(path, new_path)
        if type != 1 and type != 2:
            return 0xDEAD

        try:
            path.rename(new_path)
        except:
            return 0xDEAD

    @command_handler(CommandId.GetSpecialPathCount)
    def get_special_path_count(self):
        self.write(c_uint32(len(self.special_paths)))

    @command_handler(CommandId.GetSpecialPath)
    def get_special_path(self, idx: c_uint32):
        if idx < len(self.special_paths):
            path = self.special_paths[idx]
            self.write(path.name, path)
        else:
            return 0xDEAD

    @command_handler(CommandId.SelectFile)
    def select_file(self):
        if self.selected_path is None:
            path = Path(input("Select file: "))
            self.write(path)
        else:
            self.write(self.selected_path)

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("-f", "--selected-file", type=Path, default=None)
    parser.add_argument("drive_paths", nargs="*", type=Path)
    args = parser.parse_args()

    if args.selected_file is None and len(args.drive_paths) >= 1 and args.drive_paths[0].is_file():
        args.selected_file = args.drive_paths[0]
        args.drive_paths = args.drive_paths[1:]

    c = CommandProcessor(args.drive_paths, selected_path=args.selected_file)
    c.loop()
