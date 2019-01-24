#!/usr/bin/env python3

import usb.core
import usb.util
import struct
import sys
import os

from PFS0 import PFS0

def get_switch():
    dev=usb.core.find(idVendor=0x057e, idProduct=0x3000)
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

class CommandId:
    ConnectionRequest=0
    ConnectionResponse=1
    NSPName=2
    Start=3
    NSPData=4
    NSPContent=5
    NSPTicket=6
    Finish=7

class Command:
    GLUC=0x43554c47
    def __init__(self,cmd_id=0,raw=None):
        if raw is None:
            self.cmd_id=cmd_id
            self.magic=self.GLUC
        else:
            self.magic,self.cmd_id=struct.unpack("=II",raw)
    def magic_ok(self):
        return self.magic==self.GLUC
    def has_id(self,cmd_id):
        return self.cmd_id==cmd_id
    def __bytes__(self):
        return struct.pack("=II",self.magic,self.cmd_id)

dev=get_switch()
ep=get_ep(dev)
def write(buffer,timeout=None):
    ep[0].write(buffer,timeout=timeout)
def read(length,timeout=None):
    return ep[1].read(length,timeout=timeout).tobytes()

invalid_cmd="An invalid command was received. Are you sure Goldleaf is active?"
install_cancelled="Goldleaf has canceled the installation."

def main():
    c=Command()
    write(bytes(c))
    c=Command(raw=read(8))
    if c.magic_ok():
        if c.has_id(CommandId.ConnectionResponse):
            print("Connection was established with Goldleaf.")
            c=Command(CommandId.NSPName)
            write(bytes(c))
            base_name=os.path.basename(sys.argv[1])
            write(struct.pack("=I",len(base_name)))
            write(base_name.encode())
            print("NSP name sent to Goldleaf")
            resp=None
            while resp is None:
                try:
                    resp=read(8)
                    c=Command(raw=resp)
                except usb.core.USBError:
                    pass
            if c.magic_ok():
                if c.has_id(CommandId.Start):
                    print("Goldleaf is ready for the installation. Preparing everything...")
                    pnsp=PFS0(sys.argv[1])
                    c=Command(CommandId.NSPData)
                    write(bytes(c))
                    write(struct.pack("=I",len(pnsp.files)))
                    tik_idx=-1
                    tmp_idx=0
                    for file in pnsp.files:
                        write(struct.pack("=I",len(file.name)))
                        write(file.name.encode())
                        write(struct.pack("=Q",pnsp.header_size+file.file_offset))
                        write(struct.pack("=Q",file.file_size))
                        if os.path.splitext(file.name)[1][1:].lower()=="tik":
                            tik_idx=tmp_idx
                        tmp_idx+=1
                    while True:
                        c=Command(raw=read(8))
                        if c.magic_ok():
                            if c.has_id(CommandId.NSPContent):
                                idx=struct.unpack("=I",read(4))[0]
                                print("Sending content '"+pnsp.files[idx].name+"'... ("+str(idx+1)+" of "+str(len(pnsp.files))+")")
                                for buf in pnsp.read_chunks(idx):
                                    write(buf)
                                print("Content was sent to Goldleaf.")
                            elif c.has_id(CommandId.NSPTicket):
                                print("Sending ticket file...")
                                write(pnsp.read_file(tik_idx))
                            elif c.has_id(CommandId.Finish):
                                break
                        else:
                            print(invalid_cmd)
                            return 1
                elif c.has_id(CommandId.Finish):
                    print(install_cancelled)
                else:
                    print(invalid_cmd)
                    return 1
            else:
                print(invalid_cmd)
                return 1
        elif c.has_id(CommandId.Finish):
            print(install_cancelled)
        else:
            print(invalid_cmd)
            return 1
    else:
        print(invalid_cmd)
        return 1
    print("The installation has finished.")
    #c=Command(CommandId.Finish)
    #write(bytes(c))
    return 0

if __name__=="__main__":
    sys.exit(main())

