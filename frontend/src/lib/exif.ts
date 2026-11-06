// exifr 体积较大，改为调用时动态加载，避免进入 Studio 首屏 chunk
export interface ExtractedExif {
  camera?: string;
  lens?: string;
  focalLength?: string;
  aperture?: string;
  shutterSpeed?: string;
  iso?: string;
  date?: string;
  location?: string;
  width?: number;
  height?: number;
}

// Reads EXIF metadata from a picked image file in the browser (admin upload flow).
export const extractExifFromFile = async (file: File): Promise<ExtractedExif> => {
  try {
    const exifr = (await import('exifr')).default;
    const raw = await exifr.parse(file, {
      tiff: true,
      exif: true,
      gps: true,
      interop: true,
    });

    if (!raw) return {};

    const make = raw.Make ? raw.Make.trim() : '';
    const model = raw.Model ? raw.Model.trim() : '';
    let camera = model;
    if (make && !model.toLowerCase().includes(make.toLowerCase())) {
      camera = `${make} ${model}`;
    }

    const lens = raw.LensModel || raw.Lens || '';
    const focalLength = raw.FocalLength ? `${Math.round(raw.FocalLength)}mm` : '';
    const aperture = raw.FNumber ? `f/${raw.FNumber}` : '';

    let shutterSpeed = '';
    if (raw.ExposureTime) {
      if (raw.ExposureTime < 1) {
        const denom = Math.round(1 / raw.ExposureTime);
        shutterSpeed = `1/${denom}s`;
      } else {
        shutterSpeed = `${raw.ExposureTime}s`;
      }
    }

    const iso = raw.ISO ? `${raw.ISO}` : '';
    let date = '';
    if (raw.DateTimeOriginal || raw.CreateDate) {
      const d = new Date(raw.DateTimeOriginal || raw.CreateDate);
      if (!isNaN(d.getTime())) {
        date = d.toISOString().split('T')[0];
      }
    }

    let location = '';
    if (raw.latitude && raw.longitude) {
      location = `GPS: ${raw.latitude.toFixed(2)}°, ${raw.longitude.toFixed(2)}°`;
    }

    return {
      camera: camera || undefined,
      lens: lens || undefined,
      focalLength: focalLength || undefined,
      aperture: aperture || undefined,
      shutterSpeed: shutterSpeed || undefined,
      iso: iso || undefined,
      date: date || undefined,
      location: location || undefined,
      width: raw.ExifImageWidth || raw.ImageWidth,
      height: raw.ExifImageHeight || raw.ImageHeight,
    };
  } catch (err) {
    console.warn('Failed to parse EXIF:', err);
    return {};
  }
};
