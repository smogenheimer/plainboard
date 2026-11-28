const VIDEO_EXT = /\.(webm|mp4|m4v|mov|ogv|ogg)$/i;

interface FileLike {
  filename: string;
  content_type: string;
}

/** Told by the content type, or by the file name where the uploader's browser sent only a generic type. */
export const isVideoFile = (file: FileLike) =>
  file.content_type.startsWith("video/") || VIDEO_EXT.test(file.filename);
export const isImageFile = (file: FileLike) =>
  file.content_type.startsWith("image/");
/** What the viewer shows: pictures and videos. */
export const isMediaFile = (file: FileLike) =>
  isImageFile(file) || isVideoFile(file);
/** A picture attachment at about this width (CSS pixels × screen density): a small preview made by the server,
 * the original where none can be made. The original stays at /api/attachments/<id>. */
export const thumbSrc = (id: number, width: number) =>
  `/api/attachments/${id}?w=${width}`;
/** A picture in Markdown that is an attachment of this PlainBoard: shown as a preview of this width. */
export function previewSrc(src: string, width: number) {
  const own = src.startsWith(window.location.origin) ? src.slice(window.location.origin.length) : src;
  const id = /^\/api\/attachments\/(\d+)(\?inline=(?:true|false))?$/.exec(own)?.[1];
  return id ? thumbSrc(Number(id), width) : src;
}
export const isVideoName = (name: string) =>
  VIDEO_EXT.test(name.split(/[?#]/)[0]);
