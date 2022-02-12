#!/usr/bin/env python3

import enum
import inspect
import io
import shutil
import usb.core
import usb.util
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

class Result(enum.Enum):
    Success           = 0x0000
    ExceptionCaught   = 0xBAF1
    InvalidIndex      = 0xBAF2
    InvalidFileMode   = 0xBAF3
    SelectionCanceled = 0xBAF4

class PathType(enum.Enum):
    Invalid   = 0
    File      = 1
    Directory = 2

class FileMode(enum.Enum):
    Read   = 1
    Write  = 2
    Append = 3

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
            return func(self, **{x: self.read(y) for x, y in inspect.get_annotations(func).items()})

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
                    tmp = self.read_buf.read(size)
                    ret.append(tmp.decode("utf-8"))

                elif arg == Path:
                    path = self.read(str)
                    drive, relative_path = path.split(":/", 1)

                    try:
                        drive_path = self.drives[drive]
                    except KeyError:
                        raise ValueError(f"Drive not found: {drive}")

                    ret.append(Path(drive_path, relative_path))

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
                self.write(arg.encode("utf-8"))
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

    def list_files(self, path):
        return [x for x in path.iterdir() if x.is_file()]

    def list_directories(self, path):
        return [x for x in path.iterdir() if x.is_dir()]

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

    def send(self, result):
        response_buf = io.BytesIO()
        response_buf.write(self.out_magic)
        response_buf.write(c_uint32(result.value))

        self.write_buf.seek(0)
        response_buf.write(self.write_buf.read())

        response_buf.write(b"\x00" * (self.block_size - response_buf.tell()))

        response_buf.seek(0)
        self.usb.write(response_buf.read())

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

            result = self.command_handlers[id]()
            if result != Result.Success:
                print(f"An error occured: {result}")

            self.send(result)

    @command_handler(CommandId.GetDriveCount)
    def get_drive_count(self):
        self.write(c_uint32(len(self.drives)))

        return Result.Success

    @command_handler(CommandId.GetDriveInfo)
    def get_drive_info(self, index: c_uint32):
        if index >= len(self.drives):
            return Result.InvalidIndex

        drive = tuple(self.drives.keys())[index]

        # Label and prefix
        self.write(drive, drive)

        # Intended to be total and free space, currently not used.
        self.write(c_uint64(0), c_uint64(0))

        return Result.Success

    @command_handler(CommandId.StatPath)
    def stat_path(self, path: Path):
        try:
            path_type = PathType.Invalid
            file_size = 0

            if path.is_file():
                path_type = PathType.File
                file_size = path.stat().st_size
            elif path.is_dir():
                path_type = PathType.Directory

            self.write(c_uint32(path_type.value), c_uint64(file_size))
        except Exception:
            return Result.ExceptionCaught

        return Result.Success

    @command_handler(CommandId.GetFileCount)
    def get_file_count(self, path: Path):
        files = self.list_files(path)
        self.write(c_uint32(len(files)))

        return Result.Success

    @command_handler(CommandId.GetFile)
    def get_file(self, path: Path, index: c_uint32):
        files = self.list_files(path)

        try:
            file = files[index]
        except IndexError:
            return Result.InvalidIndex

        self.write(file.name)

        return Result.Success

    @command_handler(CommandId.GetDirectoryCount)
    def get_directory_count(self, path: Path):
        directories = self.list_directories(path)
        self.write(c_uint32(len(directories)))

        return Result.Success

    @command_handler(CommandId.GetDirectory)
    def get_directory(self, path: Path, index: c_uint32):
        directories = self.list_directories(path)

        try:
            directory = directories[index]
        except IndexError:
            return Result.InvalidIndex

        self.write(directory.name)

        return Result.Success

    @command_handler(CommandId.StartFile)
    def start_file(self, path: Path, mode: c_uint32):
        try:
            mode = FileMode(mode)
        except ValueError:
            return Result.InvalidFileMode

        try:
            match mode:
                case FileMode.Read:
                    self.read_file.close()
                    self.read_file = path.open("rb")

                case FileMode.Write:
                    self.write_file.close()
                    self.write_file = path.open("wb")

                case FileMode.Append:
                    self.write_file.close()
                    self.write_file = path.open("ab")

        except Exception:
            return Result.ExceptionCaught

        return Result.Success

    @command_handler(CommandId.ReadFile)
    def read_file(self, path: Path, offset: c_uint64, size: c_uint64):
        try:
            buf = b""

            if self.read_file.closed:
                with path.open("rb") as f:
                    f.seek(offset)
                    buf = f.read(size)
            else:
                self.read_file.seek(offset)
                buf = self.read_file.read(size)

            self.write(c_uint64(len(buf)))
            self.add_buffer(buf)
        except Exception:
            return Result.ExceptionCaught

        return Result.Success

    @command_handler(CommandId.WriteFile)
    def write_file(self, path: Path, size: c_uint64):
        buf = self.get_buffer(size)

        try:
            if self.write_file.closed:
                with path.open("wb") as f:
                    f.write(buf)
            else:
                self.write_file.write(buf)

            self.write(c_uint64(size))
        except Exception:
            return Result.ExceptionCaught

        return Result.Success

    @command_handler(CommandId.EndFile)
    def end_file(self, mode: c_uint32):
        try:
            mode = FileMode(mode)
        except ValueError:
            return Result.InvalidFileMode

        try:
            match mode:
                case FileMode.Read:
                    self.read_file.close()

                case FileMode.Write | FileMode.Append:
                    self.write_file.close()

        except Exception:
            return Result.ExceptionCaught

        return Result.Success

    @command_handler(CommandId.Create)
    def create(self, path: Path, path_type: c_uint32):
        try:
            path_type = PathType(path_type)
        except ValueError:
            path_type = PathType.Invalid

        try:
            match path_type:
                case PathType.File:
                    path.touch()

                case PathType.Directory:
                    path.mkdir()

        except Exception:
            return Result.ExceptionCaught

        return Result.Success

    @command_handler(CommandId.Delete)
    def delete(self, path: Path):
        try:
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
        except Exception:
            return Result.ExceptionCaught

        return Result.Success

    @command_handler(CommandId.Rename)
    def rename(self, path: Path, new_path: Path):
        try:
            path.rename(new_path)
        except Exception:
            return Result.ExceptionCaught

        return Result.Success

    @command_handler(CommandId.GetSpecialPathCount)
    def get_special_path_count(self):
        self.write(c_uint32(len(self.special_paths)))

        return Result.Success

    @command_handler(CommandId.GetSpecialPath)
    def get_special_path(self, index: c_uint32):
        try:
            path = self.special_paths[index]
        except IndexError:
            return Result.InvalidIndex

        self.write(path.name, path)

        return Result.Success

    @command_handler(CommandId.SelectFile)
    def select_file(self):
        if self.selected_path is None:
            try:
                print()
                path = Path(input("Select file (use CTRL-C to cancel): "))
                print()
            except KeyboardInterrupt:
                print()

                return Result.SelectionCanceled

            self.write(path)
        else:
            self.write(self.selected_path)

        return Result.Success

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
