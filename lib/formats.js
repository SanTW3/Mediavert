// Общие данные о форматах: используются и в main-процессе, и в окнах (через preload)

// Целевые форматы: ключ -> [расширение, подпись]
const FORMAT_LIST = {
  audio: [['mp3', 'mp3', 'MP3'], ['wav', 'wav', 'WAV'], ['aac', 'm4a', 'AAC (.m4a)'], ['flac', 'flac', 'FLAC'], ['alac', 'm4a', 'ALAC (.m4a)'], ['aiff', 'aiff', 'AIFF'], ['ogg', 'ogg', 'OGG Vorbis'], ['opus', 'opus', 'Opus']],
  video: [['mp4', 'mp4', 'MP4 (H.264)'], ['mov', 'mov', 'MOV (H.264)'], ['prores', 'mov', 'MOV (ProRes)'], ['mkv', 'mkv', 'MKV'], ['webm', 'webm', 'WebM (VP9)'], ['gif', 'gif', 'GIF']],
  image: [['png', 'png', 'PNG'], ['jpg', 'jpg', 'JPG'], ['webp', 'webp', 'WebP'], ['bmp', 'bmp', 'BMP'], ['tiff', 'tiff', 'TIFF'], ['ico', 'ico', 'ICO']],
  // распознавание речи из аудио и видео
  text: [['txt', 'txt', 'Текст (.txt)'], ['srt', 'srt', 'Субтитры (.srt)']],
};

const FORMATS = {};
for (const [kind, list] of Object.entries(FORMAT_LIST)) {
  FORMATS[kind] = Object.fromEntries(list.map(([key, ext]) => [key, ext]));
}

// Расширения исходных файлов
const EXT = {
  audio: 'wav mp3 flac aiff aif aifc ogg oga opus m4a aac wma alac amr ape wv ac3 mka caf mp2 au snd'.split(' '),
  video: 'mp4 mov mkv avi webm wmv flv m4v mpg mpeg 3gp ts mts m2ts vob ogv gif'.split(' '),
  image: 'png jpg jpeg jfif webp bmp tif tiff ico tga'.split(' '),
};

// Что можно получить из исходника каждого типа
const TARGETS = {
  audio: ['audio', 'text'],
  video: ['video', 'audio', 'image', 'text'],
  image: ['image'],
};

const KIND_LABEL = { audio: 'Аудио', video: 'Видео', image: 'Картинка', text: 'Текст' };

function kindOf(format) {
  return Object.keys(FORMATS).find((k) => format in FORMATS[k]) || null;
}

function extOf(name) {
  const m = /\.([^.\\/]+)$/.exec(String(name));
  return m ? m[1].toLowerCase() : '';
}

function kindOfFile(name) {
  const ext = extOf(name);
  return Object.keys(EXT).find((k) => EXT[k].includes(ext)) || null;
}

function canConvert(srcKind, targetKind) {
  return !!srcKind && TARGETS[srcKind].includes(targetKind);
}

module.exports = { FORMAT_LIST, FORMATS, EXT, TARGETS, KIND_LABEL, kindOf, extOf, kindOfFile, canConvert };
