# fatshuffle.py - Shuffle clusters in a FAT filesystem image

This tool will shuffle the clusters in a FAT16 disk image. It will result in a very fragmented file system.

### Why?

Because you can. And because you want to see the Windows 95 `DEFRAG.EXE` work very hard.

### Limitations

* Only FAT16 is supported at this point.
* Only disk images created by Win9x have been tested. It might randomly break on file systems created by other systems.
