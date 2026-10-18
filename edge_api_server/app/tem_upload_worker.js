/* TEM checksum worker: ES5, offline and bounded to a 256 KiB read buffer. */
(function () {
  var table = new Uint32Array(256);
  for (var i = 0; i < 256; i += 1) {
    var value = i;
    for (var bit = 0; bit < 8; bit += 1) value = (value & 1) ? (0xEDB88320 ^ (value >>> 1)) : (value >>> 1);
    table[i] = value >>> 0;
  }
  self.onmessage = function (event) {
    var task = event.data;
    try {
      var reader = new FileReaderSync(), crc = 0xFFFFFFFF;
      for (var offset = 0; offset < task.blob.size; offset += 256 * 1024) {
        var bytes = new Uint8Array(reader.readAsArrayBuffer(task.blob.slice(offset, offset + 256 * 1024)));
        for (var j = 0; j < bytes.length; j += 1) crc = table[(crc ^ bytes[j]) & 255] ^ (crc >>> 8);
        bytes = null;
      }
      var hex = ((crc ^ 0xFFFFFFFF) >>> 0).toString(16);
      self.postMessage({id: task.id, crc32: ('00000000' + hex).slice(-8)});
    } catch (_error) { self.postMessage({id: task.id, error: true}); }
  };
}());
