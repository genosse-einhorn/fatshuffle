#!/usr/bin/env python3

# fatshuffle.py
# Copyright © 2019 Jonas Kümmerlin <jonas@kuemmerlin.eu>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

# NOTE: only FAT16 disk images are supported at this point

from argparse import ArgumentParser, FileType
from mmap import mmap
from math import ceil
import struct
import random

class FatDirectoryEntry:
    def __init__(self, data, offset=0):
        self.raw = data
        self.raw_offset = offset

    @property
    def filename(self):
        name = self.raw[self.raw_offset+0:self.raw_offset+8].strip()
        extension = self.raw[self.raw_offset+8:self.raw_offset+11].strip()

        if len(extension):
            return name + b'.' + extension
        else:
            return name

    @property
    def is_readonly(self):
        return (self.raw[self.raw_offset+0x0b] & 0x01) > 0

    @property
    def is_hidden(self):
        return (self.raw[self.raw_offset+0x0b] & 0x02) > 0

    @property
    def is_system(self):
        return (self.raw[self.raw_offset+0x0b] & 0x04) > 0

    @property
    def is_directory(self):
        return (self.raw[self.raw_offset+0x0b] & 0x10) > 0

    @property
    def is_volume_label(self):
        return (self.raw[self.raw_offset+0x0b] & 0x08) > 0

    @property
    def start_cluster(self):
        c, = struct.unpack('<H', self.raw[self.raw_offset+0x1a:self.raw_offset+0x1c])
        return c

    @start_cluster.setter
    def start_cluster(self, c):
        self.raw[self.raw_offset+0x1a:self.raw_offset+0x1c] = struct.pack('<H', c)

    @property
    def file_size(self):
        size, = struct.unpack('<I', self.raw[self.raw_offset+0x1c:self.raw_offset+0x20])

        return size

    @property
    def is_available(self):
        return self.raw[self.raw_offset+0] == 0 or self.raw[self.raw_offset+0] == 0xe5

    @property
    def is_end_of_directory(self):
        return self.raw[self.raw_offset+0] == 0

class FatImageAccessor:
    def __init__(self, image, offset=0):
        self.data = image
        self.data_offset = offset

        # initialize stuff from image
        self.sectorsize, self.sectorspercluster, self.reservedsectors, self.numberoffats, self.rootdirentrycount, self.totalsectorcount16, self.mediadescriptor, self.sectorsperfat, self.sectorspertrack, self.numheads, self.hiddsec, self.totalsectorcount32 = struct.unpack('<HBHBHHBHHHII', self.data[self.data_offset+0xb:self.data_offset+0x24])

        self.fat_offset = self.data_offset + self.sectorsize * self.reservedsectors
        self.rootdir_offset = self.data_offset + self.sectorsize * (self.reservedsectors + self.numberoffats * self.sectorsperfat)
        self.dataarea_offset = self.data_offset + self.sectorsize * (self.reservedsectors + self.numberoffats * self.sectorsperfat + ceil((32 * self.rootdirentrycount) / self.sectorsize))

        self.totalsectorcount = self.totalsectorcount16 or self.totalsectorcount32

        self.number_of_clusters = 2 + (self.totalsectorcount - self.reservedsectors - self.numberoffats * self.sectorsperfat - ceil((32 * self.rootdirentrycount) / self.sectorsize)) // self.sectorspercluster

        assert self.number_of_clusters >= 4086 and self.number_of_clusters <= 65525

    def cluster_offset(self, cn):
        return self.dataarea_offset + self.sectorsize * self.sectorspercluster * (cn - 2)

    def read_clusterdata(self, cn):
        offset = self.cluster_offset(cn)
        size = self.sectorsize * self.sectorspercluster

        return self.data[offset:offset+size]

    def write_clusterdata(self, cn, data):
        offset = self.cluster_offset(cn)
        size = self.sectorsize * self.sectorspercluster

        assert len(data) == size

        self.data[offset:offset+size] = data

    def get_cluster_no_chain(self, start):
        endmarker, = struct.unpack('<H', self.data[self.fat_offset + 2:self.fat_offset + 4])
        r = []

        while start != endmarker and start >= 2 and start < 0xfff8:
            r.append(start)

            offset = self.fat_offset + start*2
            follow, = struct.unpack('<H', self.data[offset:offset+2])

            start = follow

        return r

    def shuffle_clusters(self):
        # this is where we make the donuts

        clustermap = [0, 1] + random.SystemRandom().sample(range(2, self.number_of_clusters), k=self.number_of_clusters-2)

        # swap clusters
        swapmap = list(clustermap)
        for i in range(2, len(swapmap)):
            target = swapmap[i]

            if target == i:
                continue

            data = self.read_clusterdata(i)
            swapmap[i] = None

            while target is not None:
                nexttarget = swapmap[target]
                nextdata = self.read_clusterdata(target)

                self.write_clusterdata(target, data)
                swapmap[target] = target

                target = nexttarget
                data = nextdata

        # build new fats
        oldfat = self.data[self.fat_offset:self.fat_offset + self.sectorsperfat * self.sectorsize]
        for k in range(0, self.numberoffats):
            fatoffset = self.fat_offset + k * self.sectorsperfat * self.sectorsize
            for i in range(2, len(clustermap)):
                offset = fatoffset + clustermap[i] * 2
                o, = struct.unpack('<H', oldfat[2*i:2*i+2])

                if o < len(clustermap):
                    n = clustermap[o]
                else:
                    n = o

                self.data[offset:offset+2] = struct.pack('<H', n)

            # set scandisk flag
            self.data[fatoffset+3] = self.data[fatoffset+3] & 0x7f

        # fixup directories
        def fixup_dir(entries):
            for e in entries:
                if e.start_cluster < len(clustermap):
                    e.start_cluster = clustermap[e.start_cluster]

                if e.is_volume_label:
                    continue

                if e.is_directory and e.filename != b'.' and e.filename != b'..':
                    fixup_dir(self.get_dir_entries(e.start_cluster))

        fixup_dir(self.root_dir_entries)

    def debug_dir(self, entries, indent=''):
        for e in entries:
            if e.is_volume_label:
                continue

            if e.is_directory:
                print('{}{}    {}'.format(indent, e.filename + b'/', ', '.join(map(str, self.get_cluster_no_chain(e.start_cluster)))))

                if e.filename != b'.' and e.filename != b'..':
                    self.debug_dir(self.get_dir_entries(e.start_cluster), indent + '    ')
            else:
                print('{}{}    {}'.format(indent, e.filename, ', '.join(map(str, self.get_cluster_no_chain(e.start_cluster)))))

    def debug(self):
        print('sector size: {}'.format(self.sectorsize))
        print('sectors per cluster: {}'.format(self.sectorspercluster))
        print('number of clusters: {}'.format(self.number_of_clusters))
        print('rootdir offset: 0x{:X}'.format(self.rootdir_offset))
        print('data offset: 0x{:X}'.format(self.dataarea_offset))
        print('number of fats: {}'.format(self.numberoffats))
        print('number of rootdir entries: {}'.format(self.rootdirentrycount))
        print('')
        self.debug_dir(self.root_dir_entries)

    def get_dir_entries(self, startcluster):
        for cluster in self.get_cluster_no_chain(startcluster):
            clusteroffset = self.cluster_offset(cluster)
            for i in range(0, self.sectorspercluster * self.sectorsize // 32):
                e = FatDirectoryEntry(self.data, clusteroffset + i*32)
                if e.is_end_of_directory:
                    break
                if e.is_available:
                    continue

                yield e

    @property
    def root_dir_entries(self):
        for i in range(0, self.rootdirentrycount):
            e = FatDirectoryEntry(self.data, self.rootdir_offset + i*32)
            if e.is_end_of_directory:
                break
            if e.is_available:
                continue

            yield e


ap = ArgumentParser()
ap.add_argument('--offset', help='offset into image', type=int, default=0)
ap.add_argument('--debug', help='list files in image', action='store_true')
ap.add_argument('image', help='path to FAT16 image file', type=FileType('r+b'))

args = ap.parse_args()

with mmap(args.image.fileno(), 0) as f:
    fat = FatImageAccessor(f, offset=args.offset)

    if args.debug:
        fat.debug()
    else:
        fat.shuffle_clusters()
