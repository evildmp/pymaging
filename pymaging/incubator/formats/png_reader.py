# -*- coding: utf-8 -*-
# http://www.w3.org/TR/PNG-DataRep.html
from __future__ import generators
from array import array
from pymaging.colors import RGBA, RGB
from pymaging.exceptions import PymagingException
from pymaging.image import Image
from pymaging.utils import fdiv
import math
import operator
import struct
import warnings
import zlib
#
# Based on http://code.google.com/p/pypng/source/browse/trunk/code/png.py
#
# png.py - PNG encoder/decoder in pure Python
#
# Copyright (C) 2006 Johann C. Rocholl <johann@browsershots.org>
# Portions Copyright (C) 2009 David Jones <drj@pobox.com>
# And probably portions Copyright (C) 2006 Nicko van Someren <nicko@nicko.org>
#
# Original concept by Johann C. Rocholl.
#
# LICENSE (The MIT License)
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
# BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
# Changelog (recent first):
# 2009-03-11 David: interlaced bit depth < 8 (writing).
# 2009-03-10 David: interlaced bit depth < 8 (reading).
# 2009-03-04 David: Flat and Boxed pixel formats.
# 2009-02-26 David: Palette support (writing).
# 2009-02-23 David: Bit-depths < 8; better PNM support.
# 2006-06-17 Nicko: Reworked into a class, faster interlacing.
# 2006-06-17 Johann: Very simple prototype PNG decoder.
# 2006-06-17 Nicko: Test suite with various image generators.
# 2006-06-17 Nicko: Alpha-channel, grey-scale, 16-bit/plane support.
# 2006-06-15 Johann: Scanline iterator interface for large input files.
# 2006-06-09 Johann: Very simple prototype PNG encoder.

# Incorporated into Bangai-O Development Tools by drj on 2009-02-11 from
# http://trac.browsershots.org/browser/trunk/pypng/lib/png.py?rev=2885

# Incorporated into pypng by drj on 2009-03-12 from
# //depot/prj/bangaio/master/code/png.py#67


# http://www.python.org/doc/2.2.3/whatsnew/node5.html

try: # See :pyver:old
    import itertools
except:
    pass
# http://www.python.org/doc/2.4.4/lib/module-operator.html
# http://www.python.org/doc/2.4.4/lib/module-warnings.html


# The PNG signature.
# http://www.w3.org/TR/PNG/#5PNG-file-signature
PNG_SIGNATURE = struct.pack('8B', 137, 80, 78, 71, 13, 10, 26, 10)
MAX_CHUNK_LENGTH = 2**31-1
VERIFY_CONSTANT = 2**32 - 1
ALLOWED_BIT_DEPTHS = [1, 2, 4, 8, 16]
ALLOWED_COLOR_TYPES = [0, 2, 3, 4, 6]

ADAM7 = ((0, 0, 8, 8),
         (4, 0, 8, 8),
         (0, 4, 4, 8),
         (2, 0, 4, 4),
         (0, 2, 2, 4),
         (1, 0, 2, 2),
         (0, 1, 1, 2))

def group(s, n):
    # See
    # http://www.python.org/doc/2.6/library/functions.html#zip
    return zip(*[iter(s)]*n)

try:  # see :pyver:old
    array.tostring
except:
    def tostring(row):
        l = len(row)
        return struct.pack('%dB' % l, *row)
else:
    def tostring(row):
        """Convert row of bytes to string.  Expects `row` to be an
        ``array``.
        """
        return row.tostring()

# Conditionally convert to bytes.  Works on Python 2 and Python 3.
try:
    bytes('', 'ascii')
    def strtobytes(x): return bytes(x, 'iso8859-1')
    def bytestostr(x): return str(x, 'iso8859-1')
except:
    strtobytes = str
    bytestostr = str


class PNGReaderError(PymagingException): pass
class NoChunkLength(PNGReaderError): pass
class NoChunkType(PNGReaderError): pass
class InvalidChunkLength(PNGReaderError): pass
class InvalidChunkType(PNGReaderError): pass
class ChunkError(PNGReaderError): pass


def nofilter(scanline, previous, filter_unit):
    return scanline

def sub(scanline, previous, filter_unit):
    """Undo sub filter."""

    result = array('B', scanline)
    # Loops starts at index filter_unit.  Observe that the initial part
    # of the result is already filled in correctly with scanline.
    for ai, i in enumerate(range(filter_unit, len(scanline))):
        x = scanline[i]
        a = result[ai]
        result[i] = (x + a) & 0xff
    return result

def up(scanline, previous, filter_unit):
    """Undo up filter."""
    scanline_length = len(scanline)
    if previous is None:
        previous = array('B', [0] * scanline_length)
    return array('B', [scanline[index] + previous[index] & 0xff for index in range(scanline_length)])

def average(scanline, previous, filter_unit):
    """Undo average filter."""

    ai = -filter_unit
    result = array('B', scanline)
    for i in range(len(result)):
        x = scanline[i]
        if ai < 0:
            a = 0
        else:
            a = result[ai]
        b = previous[i]
        result[i] = (x + ((a + b) >> 1)) & 0xff
        ai += 1
    return result

def paeth(scanline, previous, filter_unit):
    """Undo Paeth filter."""

    result = array('B', scanline)
    # Also used for ci.
    ai = -filter_unit
    for i in range(len(result)):
        x = scanline[i]
        if ai < 0:
            a = c = 0
        else:
            a = result[ai]
            c = previous[ai]
        b = previous[i]
        p = a + b - c
        pa = abs(p - a)
        pb = abs(p - b)
        pc = abs(p - c)
        if pa <= pb and pa <= pc:
            pr = a
        elif pb <= pc:
            pr = b
        else:
            pr = c
        result[i] = (x + pr) & 0xff
        ai += 1
    return result

FILTERS = {
    0: nofilter,
    1: sub,
    2: up, 
    3: average,
    4: paeth,
}

class Reader(object):
    def __init__(self, fileobj):
        self.fileobj = fileobj
        # set some attributes that need to be filled by pre-IDAT chunks
        self.done_reading = False # set by IEND
        self.plte = None
        self.palette = None
        self.width = None
        self.height = None
        self.bit_depth = None
        self.color_type = None
        self.compression_method = None
        self.filter_method = None
        self.interlace_method = None
        self.colormap = None
        self.greyscale = None
        self.alpha = None
        self.color_planes = None
        self.planes = None
        self.psize = None
        self.row_bytes = None
        self.transparent = None
        self.background = None
        self.sbit = None
        self.trns = None
        self.decompressor = zlib.decompressobj()
        self.raw = array('B')
        self.pixels = array('B')
        
    def get_image(self):
        self.validate_signature()
        nullhandler = lambda chunk, length: None
        handlers = {
            'IHDR': self.handle_chunk_IHDR,
            'PLTE': self.handle_chunk_PLTE,
            'IDAT': self.handle_chunk_IDAT,
            'bKGD': self.handle_chunk_bKGD,
            'tRNS': self.handle_chunk_tRNS,
            'gAMA': self.handle_chunk_gAMA,
            'sBIT': self.handle_chunk_sBIT,
            'IEND': self.handle_chunk_IEND,
        }
        for chunk_type, chunk_length, chunk_data in self.iter_chunks():
            handler = handlers.get(chunk_type, nullhandler)
            handler(chunk_data, chunk_length)
        mode = RGBA if self.alpha else RGB
        return Image(self.width, self.height, self.pixels, mode, palette=self.palette)
        
    def validate_signature(self):
        """
        Validates the signature, should only ever be called once.
        """
        signature = self.fileobj.read(8)
        if signature != PNG_SIGNATURE:
            raise PNGReaderError("PNG file has invalid signature.")
            
    def iter_chunks(self):
        while not self.done_reading:
            # chunk_length
            raw_chunk_length = self.fileobj.read(4)
            if not raw_chunk_length:
                raise NoChunkLength()
            if len(raw_chunk_length) != 4:
                raise InvalidChunkLength(raw_chunk_length)
            chunk_length = struct.unpack('!I', raw_chunk_length)[0]
            
            # chunk type
            raw_chunk_type = self.fileobj.read(4)
            if not raw_chunk_type:
                raise NoChunkType()
            if len(raw_chunk_type) != 4:
                raise InvalidChunkType(raw_chunk_type)
            bytes_chunk_type = struct.unpack('!4s', raw_chunk_type)[0]
            chunk_type = bytestostr(bytes_chunk_type)
            
            # sanity check
            if chunk_length > MAX_CHUNK_LENGTH:
                raise PNGReaderError('Chunk %s is too large: %d.' % (chunk_type, chunk_length))
            
            # read data
            data = self.fileobj.read(chunk_length)
            if len(data) != chunk_length:
                raise ChunkError('Chunk %s too short for required %i octets.'
                  % (chunk_type, chunk_length))
                
            # read checksum
            checksum = self.fileobj.read(4)
            if len(checksum) != 4:
                raise ChunkError('Chunk %s checksum too short.' % chunk_type)
            
            verify = zlib.crc32(bytes_chunk_type)
            verify = zlib.crc32(data, verify)
            # Whether the output from zlib.crc32 is signed or not varies
            # according to hideous implementation details, see
            # http://bugs.python.org/issue1202 .
            # We coerce it to be positive here (in a way which works on
            # Python 2.3 and older).
            verify &= VERIFY_CONSTANT
            verify = struct.pack('!I', verify)
            if checksum != verify:
                # print repr(checksum)
                (a, ) = struct.unpack('!I', checksum)
                (b, ) = struct.unpack('!I', verify)
                raise ChunkError(
                  "Checksum error in %s chunk: 0x%08X != 0x%08X." %
                  (chunk_type, a, b))
            
            yield chunk_type, chunk_length, data

    
    def handle_chunk_IHDR(self, chunk, length):
        # http://www.w3.org/TR/PNG/#11IHDR
        if length != 13:
            raise ChunkError('IHDR chunk has incorrect length %s, should be 13.' % length)
        (self.width, self.height, self.bit_depth, self.color_type,
         self.compression_method, self.filter_method,
         self.interlace_method) = struct.unpack("!2I5B", chunk)

        # Check that the header specifies only valid combinations.
        if self.bit_depth not in ALLOWED_BIT_DEPTHS:
            raise PNGReaderError("invalid bit depth %d" % self.bit_depth)
        
        if self.color_type not in ALLOWED_COLOR_TYPES:
            raise PNGReaderError("invalid colour type %d" % self.color_type)
        
        # Check indexed (palettized) images have 8 or fewer bits
        # per pixel; check only indexed or greyscale images have
        # fewer than 8 bits per pixel.
        if ((self.color_type & 1 and self.bit_depth > 8) or
            (self.bit_depth < 8 and self.color_type not in (0,3))):
            raise PNGReaderError("Illegal combination of bit depth (%d)"
              " and colour type (%d)."
              " See http://www.w3.org/TR/2003/REC-PNG-20031110/#table111 ."
              % (self.bit_depth, self.color_type))
        if self.compression_method != 0:
            raise PNGReaderError("unknown compression method %d" % self.compression_method)
        if self.filter_method != 0:
            raise PNGReaderError("Unknown filter method %d,"
              " see http://www.w3.org/TR/2003/REC-PNG-20031110/#9Filters ."
              % self.filter_method)
        if self.interlace_method not in (0, 1):
            raise PNGReaderError("Unknown interlace method %d,"
              " see http://www.w3.org/TR/2003/REC-PNG-20031110/#8InterlaceMethods ."
              % self.interlace_method)

        # Derived values
        # http://www.w3.org/TR/PNG/#6Colour-values
        colormap =  bool(self.color_type & 1)
        greyscale = not (self.color_type & 2)
        alpha = bool(self.color_type & 4)
        if greyscale or colormap:
            color_planes = 1
        else:
            color_planes = 3
        planes = color_planes + alpha

        self.colormap = colormap
        self.greyscale = greyscale
        self.alpha = alpha
        self.color_planes = color_planes
        self.planes = planes
        self.psize = fdiv(self.bit_depth, 8) * planes
        if int(self.psize) == self.psize:
            self.psize = int(self.psize)
        self.row_bytes = int(math.ceil(self.width * self.psize))
    
    def handle_chunk_PLTE(self, chunk, length):
        # http://www.w3.org/TR/PNG/#11PLTE
        if length % 3 != 0:
            raise PNGReaderError("PLTE chunk's length must be a multiple of 3.")
        if length > (2 ** self.bit_depth) * 3:
            raise PNGReaderError("PLTE chunk is too long.")
        if length == 0:
            raise PNGReaderError("Empty PLTE is not allowed.")
        # TODO: might be faster if we don't group, but makes the palette
        # interface harder to use
        self.plte = chunk
    
    def handle_chunk_bKGD(self, chunk, length):
        try:
            if self.colormap:
                if not self.palette:
                    warnings.warn("PLTE chunk is required before bKGD chunk.")
                self.background = struct.unpack('B', chunk)
            else:
                self.background = struct.unpack("!%dH" % self.color_planes, chunk)
        except struct.error:
            raise PNGReaderError("bKGD chunk has incorrect length.")
    
    def handle_chunk_tRNS(self, chunk, length):
        self.trns = chunk
        if self.colormap:
            if not self.palette:
                warnings.warn("PLTE chunk is required before tRNS chunk.")
            else:
                if length > len(self.palette):
                    # Was warning, but promoted to Error as it
                    # would otherwise cause pain later on.
                    raise PNGReaderError("tRNS chunk is too long.")
        else:
            if self.alpha:
                raise PNGReaderError(
                  "tRNS chunk is not valid with colour type %d." %
                  self.color_type)
            try:
                self.transparent = struct.unpack("!%dH" % self.color_planes, chunk)
            except struct.error:
                raise PNGReaderError("tRNS chunk has incorrect length.")
    
    def handle_chunk_gAMA(self, chunk, length):
        try:
            self.gamma = struct.unpack("!L", chunk)[0] / 100000.0
        except struct.error:
            raise PNGReaderError("gAMA chunk has incorrect length.")
    
    def handle_chunk_sBIT(self, chunk, length):
        self.sbit = chunk
        if (self.colormap and length != 3 or
            not self.colormap and length != self.planes):
            raise PNGReaderError("sBIT chunk has incorrect length.")
        
    def handle_chunk_IDAT(self, chunk, length):
        uncompressed = array('B', self.decompressor.decompress(chunk))
        self.raw.extend(uncompressed)
    
    def handle_chunk_IEND(self, chunk, length):
        """
        IEND is the last chunk, so stop reading and actually process IDAT
        """
        if self.plte:
            self._build_palette()
        self._process_pixels()
        self.done_reading = True
        
    def _build_palette(self):
        plte = group(array('B', self.plte), 3)
        if self.trns:
            trns = array('B', self.trns or '')
            trns.extend([255] * (len(plte) - len(trns)))
            plte = map(operator.add, plte, group(trns, 1))
        self.palette = plte
        
    def _process_pixels(self):
        if self.interlace_method:
            self.pixels = self._process_interlaced_pixels()
        else:
            self.pixels = self._process_non_interlaced_pixels()
            
    def _process_interlaced_pixels(self):
        if self.bit_depth > 8:
            arraycode = 'H'
        else:
            arraycode = 'B'
        return list(itertools.imap(
            lambda *row: array(arraycode, row),
            *[iter(self.deinterlace(self.raw, arraycode))] * self.width * self.planes
        ))

    def deinterlace(self, raw_row, arraycode):
        """
        Read raw pixel data, undo filters, deinterlace, and flatten.
        Return in flat row flat pixel format.
        """
        # Values per row (of the target image)
        values_per_row = self.width * self.planes

        # Make a result array, and make it big enough.  Interleaving
        # writes to the output array randomly (well, not quite), so the
        # entire output array must be in memory.
        line = array(arraycode, [0] * values_per_row * self.height)
        source_offset = 0
        filter_unit = max(1, self.psize)

        for xstart, ystart, xstep, ystep in ADAM7:
            if xstart >= self.width:
                continue
            # The previous (reconstructed) scanline.  None at the
            # beginning of a pass to indicate that there is no previous
            # line.
            recon = None
            # Pixels per row (reduced pass image)
            ppr = int(math.ceil((self.width-xstart)/float(xstep)))
            # Row size in bytes for this pass.
            row_size = int(math.ceil(self.psize * ppr))
            for y in range(ystart, self.height, ystep):
                filter_type = raw_row[source_offset]
                source_offset += 1
                scanline = raw_row[source_offset:source_offset + row_size]
                source_offset += row_size
                recon = FILTERS[filter_type](scanline, recon, filter_unit)
                # Convert so that there is one element per pixel value
                flat = self.serialtoflat(recon, ppr)
                if xstep == 1:
                    assert xstart == 0
                    offset = y * values_per_row
                    line[offset:offset + values_per_row] = flat
                else:
                    offset = y * values_per_row + xstart * self.planes
                    end_offset = (y + 1) * values_per_row
                    skip = self.planes * xstep
                    for i in range(self.planes):
                        line[offset + i:end_offset:skip] = flat[i::self.planes]
        return line

    def serialtoflat(self, bytes, width=None):
        """Convert serial format (byte stream) pixel data to flat row
        flat pixel.
        """

        if self.bit_depth == 8:
            return bytes
        if self.bit_depth == 16:
            bytes = tostring(bytes)
            return array('H',
              struct.unpack('!%dH' % (len(bytes)//2), bytes))
        assert self.bitdepth < 8
        if width is None:
            width = self.width
        # Samples per byte
        spb = 8//self.bit_depth
        out = array('B')
        mask = 2**self.bit_depth - 1
        shifts = map(self.bit_depth.__mul__, reversed(range(spb)))
        l = width
        for o in bytes:
            out.extend([(mask&(o>>s)) for s in shifts][:l])
            l -= spb
            if l <= 0:
                l = width
        return out

    def _process_non_interlaced_pixels(self):
        """
        Processes the data stored in self.raw and returns a list of arrays of
        pixels
        """
        # length of row, in bytes
        row_bytes = self.row_bytes
        row_bytes_plus_one = row_bytes + 1
        pixels = []
        filter_unit = max(1, self.psize)
        line = array('B')
        # The previous (reconstructed) scanline.  None indicates first
        # line of image.
        previous = None
        for some in self.raw:
            line.append(some)
            while len(line) >= row_bytes_plus_one:
                filter_type = line[0]
                scanline = line[1:row_bytes_plus_one]
                del line[:row_bytes_plus_one]
                reconstructed = FILTERS[filter_type](scanline, previous, filter_unit)
                previous = reconstructed
                pixels.append(self.as_values(reconstructed))
        if len(line) != 0:
            # :file:format We get here with a file format error: when the
            # available bytes (after decompressing) do not pack into exact
            # rows.
            raise PNGReaderError('Wrong size for decompressed IDAT chunk.')
        return pixels
    
    def as_values(self, raw_row):
        """Convert a row of raw bytes into a flat row.  Result may
        or may not share with argument"""

        if self.bit_depth == 8:
            return raw_row
        if self.bit_depth == 16:
            raw_row = tostring(raw_row)
            return array('H', struct.unpack('!%dH' % (len(raw_row) // 2), raw_row))
        assert self.bit_depth < 8
        width = self.width
        # Samples per byte
        spb = 8 // self.bit_depth
        out = array('B')
        mask = 2 ** self.bit_depth - 1
        shifts = map(self.bit_depth.__mul__, reversed(range(spb)))
        for o in raw_row:
            out.extend(map(lambda i: mask&(o>>i), shifts))
        return out[:width]