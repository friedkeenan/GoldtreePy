#!/usr/bin/env python3

import enum
import io
import shutil
import pak
import usb.core
import usb.util
from pathlib import Path

class String(pak.Type):
    @classmethod
    def _unpack(cls, buf, *, ctx):
        length = pak.UInt32.unpack(buf, ctx=ctx)

        return buf.read(length).decode("utf-8")

    @classmethod
    def _pack(cls, value, *, ctx):
        encoded = value.encode("utf-8")

        return pak.UInt32.pack(len(encoded), ctx=ctx) + encoded

class PathType(pak.Type):
    @classmethod
    def _unpack(cls, buf, *, ctx):
        raw_path = String.unpack(buf, ctx=ctx)

        drive, relative_path = raw_path.split(":/", 1)

        try:
            drive_path = ctx.command_handler.drives[drive]
        except KeyError:
            raise ValueError(f"Drive not found: {drive}")

        return Path(drive_path, relative_path)

    @classmethod
    def _pack(cls, value, *, ctx):
        value = value.absolute()

        for drive, drive_path in ctx.command_handler.drives.items():
            if drive_path in value.parents:
                value    = value.relative_to(drive_path)
                raw_path = f"{drive}:/{value}"

                return String.pack(raw_path, ctx=ctx)

        raise ValueError(f"Path on no known drive: {value}")

class GoldtreeContext(pak.PacketContext):
    def __init__(self, command_handler):
        self.command_handler = command_handler

class GoldtreePacket(pak.Packet):
    pass

class Command(GoldtreePacket, id_type=pak.UInt32):
    MAGIC = b"GLCI"

class Result(enum.Enum):
    Success           = 0x0000
    ExceptionCaught   = 0xBAF1
    InvalidIndex      = 0xBAF2
    InvalidFileMode   = 0xBAF3
    SelectionCanceled = 0xBAF4

class Response(GoldtreePacket):
    MAGIC = b"GLCO"

    result: pak.Enum(pak.UInt32, Result)

class CountResponse(Response):
    count: pak.UInt32

class GetDriveCountCommand(Command):
    id = 1

class GetDriveInfoCommand(Command):
    id = 2

    index: pak.UInt32

class DriveInfoResponse(Response):
    label:  String
    prefix: String

    # Unused but present fields
    total_space: pak.UInt64
    free_space:  pak.UInt64

class StatPathCommand(Command):
    id = 3

    path: PathType

class PathKind(enum.Enum):
    Invalid   = 0
    File      = 1
    Directory = 2

class StatPathResponse(Response):
    path_kind: pak.Enum(pak.UInt32, PathKind)
    file_size: pak.UInt64

class GetChildCountCommand(Command):
    directory: PathType

class GetFileCountCommand(GetChildCountCommand):
    id = 4

class GetDirectoryCountCommand(GetChildCountCommand):
    id = 6

class GetChildCommand(Command):
    directory: PathType
    index:     pak.UInt32

class GetFileCommand(GetChildCommand):
    id = 5

class GetDirectoryCommand(GetChildCommand):
    id = 7

class ChildResponse(Response):
    name: String

class FileMode(enum.Enum):
    Read   = 1
    Write  = 2
    Append = 3

class StartFileCommand(Command):
    id = 8

    path: PathType
    mode: pak.Enum(pak.UInt32, FileMode)

class SizeResponse(Response):
    size: pak.UInt64

class ReadFileCommand(Command):
    id = 9

    path:   PathType
    offset: pak.UInt64
    size:   pak.UInt64

class WriteFileCommand(Command):
    id = 10

    path: PathType
    size: pak.UInt64

class EndFileCommand(Command):
    id = 11

    mode: pak.Enum(pak.UInt32, FileMode)

class CreateCommand(Command):
    id = 12

    path:      PathType
    path_kind: pak.Enum(pak.UInt32, PathKind)

class DeleteCommand(Command):
    id = 13

    path: PathType

class RenameCommand(Command):
    id = 14

    old_path: PathType
    new_path: PathType

class GetSpecialPathCountCommand(Command):
    id = 15

class GetSpecialPathCommand(Command):
    id = 16

    index: pak.UInt32

class SpecialPathResponse(Response):
    name: String
    path: PathType

class SelectFileCommand(Command):
    id = 17

class PathResponse(Response):
    path: PathType

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

class CommandProcessor(pak.PacketHandler):
    BLOCK_SIZE = 0x1000

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

        super().__init__()

    def list_files(self, path):
        return [x for x in path.iterdir() if x.is_file()]

    def list_directories(self, path):
        return [x for x in path.iterdir() if x.is_dir()]

    def start(self):
        self.usb = UsbInterface()
        print(f"Connected to {self.usb.dev.product} - {self.usb.dev.serial_number}")

        self.read_file  = io.IOBase()
        self.write_file = io.IOBase()
        self.read_file.close()
        self.write_file.close()

        self.ctx = GoldtreeContext(self)

    def recv_command(self):
        buf = io.BytesIO(self.usb.read(self.BLOCK_SIZE, timeout=None))

        magic = buf.read(4)
        if magic != Command.MAGIC:
            print(f"Invalid input magic: {magic}")
            print(buf.getvalue())

            return None

        id = Command.unpack_id(buf, ctx=self.ctx)
        command_cls = Command.subclass_with_id(id, ctx=self.ctx)

        if command_cls is None:
            print(f"Command with unknown ID: {id}")

            return None

        return command_cls.unpack(buf, ctx=self.ctx)

    def send_response(self, response_cls, **kwargs):
        response = response_cls(ctx=self.ctx, **kwargs)

        response_buf = Response.MAGIC + response.pack(ctx=self.ctx)

        # Pad the rest of the buffer to the proper block size.
        response_buf += b"\x00" * (self.BLOCK_SIZE - len(response_buf))

        self.usb.write(response_buf)

    def send_empty_response(self, result):
        self.send_response(
            Response,

            result = result,
        )

    def send_empty_success(self):
        self.send_response(Response)

    def recv_buffer(self, size):
        return self.usb.read(size)

    def send_buffer(self, buffer):
        self.usb.write(buffer)

    def loop(self):
        self.start()

        try:
            while True:
                command = self.recv_command()
                if command is None:
                    continue

                print(f"Command: {command}")

                for listener in self.listeners_for_packet(command):
                    listener(command)

        except KeyboardInterrupt:
            pass

    @pak.packet_listener(GetDriveCountCommand)
    def get_drive_count(self, cmd):
        self.send_response(
            CountResponse,

            count = len(self.drives),
        )

    @pak.packet_listener(GetDriveInfoCommand)
    def get_drive_info(self, cmd):
        if cmd.index >= len(self.drives):
            self.send_empty_response(Result.InvalidIndex)

            return

        drive = tuple(self.drives.keys())[cmd.index]

        self.send_response(
            DriveInfoResponse,

            label  = drive,
            prefix = drive,
        )

    @pak.packet_listener(StatPathCommand)
    def stat_path(self, cmd):
        try:
            path_kind = PathKind.Invalid
            file_size = 0

            if cmd.path.is_file():
                path_kind = PathKind.File
                file_size = cmd.path.stat().st_size
            elif cmd.path.is_dir():
                path_kind = PathKind.Directory

            self.send_response(
                StatPathResponse,

                path_kind = path_kind,
                file_size = file_size,
            )
        except Exception as e:
            self.send_empty_response(Result.ExceptionCaught)

    @pak.packet_listener(GetChildCountCommand)
    def get_child_count(self, cmd):
        if isinstance(cmd, GetFileCountCommand):
            children = self.list_files(cmd.directory)
        else:
            children = self.list_directories(cmd.directory)

        self.send_response(
            CountResponse,

            count = len(children),
        )

    @pak.packet_listener(GetChildCommand)
    def get_child(self, cmd):
        if isinstance(cmd, GetFileCommand):
            children = self.list_files(cmd.directory)
        else:
            children = self.list_directories(cmd.directory)

        try:
            child = children[cmd.index]
        except IndexError:
            self.send_empty_response(Result.InvalidIndex)

        self.send_response(
            ChildResponse,

            name = child.name,
        )

    @pak.packet_listener(StartFileCommand)
    def start_file(self, cmd):
        if cmd.mode is pak.Enum.INVALID:
            self.send_empty_response(Result.InvalidFileMode)

            return

        try:
            match cmd.mode:
                case FileMode.Read:
                    self.read_file.close()
                    self.read_file = cmd.path.open("rb")

                case FileMode.Write:
                    self.write_file.close()
                    self.write_file = cmd.path.open("wb")

                case FileMode.Append:
                    self.write_file.close()
                    self.write_file = cmd.path.open("ab")

        except Exception:
            self.send_empty_response(Result.ExceptionCaught)

            return

        self.send_empty_success()

    @pak.packet_listener(ReadFileCommand)
    def read_file(self, cmd):
        try:
            if self.read_file.closed:
                with cmd.path.open("rb") as f:
                    f.seek(cmd.offset)
                    buf = f.read(cmd.size)
            else:
                self.read_file.seek(cmd.offset)
                buf = self.read_file.read(cmd.size)

            self.send_response(
                SizeResponse,

                size = len(buf),
            )

            self.send_buffer(buf)

        except Exception:
            self.send_empty_response(Result.ExceptionCaught)

    @pak.packet_listener(WriteFileCommand)
    def write_file(self, cmd):
        buf = self.recv_buffer(cmd.size)

        try:
            if self.write_file.closed:
                with cmd.path.open("wb") as f:
                    f.write(buf)
            else:
                self.write_file.write(buf)

            self.send_response(
                SizeResponse,

                size = cmd.size,
            )

        except Exception:
            self.send_empty_response(Result.ExceptionCaught)

    @pak.packet_listener(EndFileCommand)
    def end_file(self, cmd):
        if cmd.mode is pak.Enum.INVALID:
            self.send_empty_response(Result.InvalidFileMode)

            return

        try:
            match cmd.mode:
                case FileMode.Read:
                    self.read_file.close()

                case FileMode.Write | FileMode.Append:
                    self.write_file.close()

        except Exception:
            self.send_empty_response(Result.ExceptionCaught)

            return

        self.send_empty_success()

    @pak.packet_listener(CreateCommand)
    def create_file(self, cmd):
        if cmd.path_kind is pak.Enum.INVALID:
            cmd.path_kind = PathKind.Invalid

        try:
            match cmd.path_kind:
                case PathKind.File:
                    cmd.path.touch()

                case PathKind.Directory:
                    cmd.path.mkdir()

        except Exception:
            self.send_empty_response(Result.ExceptionCaught)

            return

        self.send_empty_success()

    @pak.packet_listener(DeleteCommand)
    def delete(self, cmd):
        try:
            if cmd.path.is_file():
                cmd.path.unlink()
            elif cmd.path.is_dir():
                shutil.rmtree(cmd.path)

        except Exception:
            self.send_empty_response(Result.ExceptionCaught)

            return

        self.send_empty_success()

    @pak.packet_listener(RenameCommand)
    def rename(self, cmd):
        try:
            cmd.old_path.rename(cmd.new_path)

        except Exception:
            self.send_empty_response(Result.ExceptionCaught)

            return

        self.send_empty_success()

    @pak.packet_listener(GetSpecialPathCountCommand)
    def get_special_path_count(self, cmd):
        self.send_response(
            CountResponse,

            count = len(self.special_paths)
        )

    @pak.packet_listener(GetSpecialPathCommand)
    def get_special_path(self, cmd):
        try:
            path = self.special_paths[cmd.index]
        except IndexError:
            self.send_response(
                Response,

                result = Result.InvalidIndex,
            )

            return

        self.send_response(
            SpecialPathResponse,

            name = path.name,
            path = path,
        )

    @pak.packet_listener(SelectFileCommand)
    def select_file(self, cmd):
        path = self.selected_path
        if path is None:
            try:
                print()
                path = Path(input("Select file: (Use CTRL+C to cancel): "))

            except KeyboardInterrupt:
                self.send_empty_response(Result.SelectionCanceled)

                return

            finally:
                print()

        self.send_response(
            PathResponse,

            path = path,
        )

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
