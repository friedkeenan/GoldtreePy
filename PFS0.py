import struct

class PFS0:
    class FileEntry:
        def __init__(self,data):
            self.file_offset=struct.unpack("=Q",data[:0x8])[0]
            self.file_size=struct.unpack("=Q",data[0x8:0x10])[0]
            self.name_offset=struct.unpack("=I",data[0x10:0x14])[0]
            self.name=None
    def __init__(self,filename):
        self.f=open(filename,"rb")
        if self.read_raw(0x0,0x4)!=b"PFS0":
            raise ValueError("File is not a PFS0")
        num_files=struct.unpack("=I",self.read_raw(0x4,0x4))[0]
        len_strings=struct.unpack("=I",self.read_raw(0x8,0x4))[0]
        self.files=[PFS0.FileEntry(self.read_raw(0x10+0x18*x,0x18)) for x in range(num_files)]
        self.header_size=0x10+0x18*num_files
        file_names=self.read_raw(self.header_size,len_strings).split(b"\0")[:num_files]
        for i in range(num_files):
            self.files[i].name=file_names[i].decode()
        self.header_size+=len_strings
    def read_raw(self,offset,size):
        self.f.seek(offset)
        return self.f.read(size)
    def __del__(self):
        self.f.close()
    def read_file(self,idx):
        file_entry=self.files[idx]
        return self.read_raw(self.header_size+file_entry.file_offset,file_entry.file_size)
    def read_chunks(self,idx,chunk_size=0x100000):
        file_entry=self.files[idx]
        to_read=file_entry.file_size
        cur_offset=self.header_size+file_entry.file_offset
        while to_read>0:
            tor=min(chunk_size,to_read)
            yield self.read_raw(cur_offset,tor)
            cur_offset+=tor
            to_read-=tor
