// Tianditu map service key (from existing map.html)
export const TIANDITU_KEY = '2e9d7b7e67e77affe7c75eb68b4bfcef';
export const TIANDITU_ATTRIBUTION = '&copy; <a href="https://www.tianditu.gov.cn/">天地图</a>';

export const DEFAULT_LAT = 30.2741;
export const DEFAULT_LON = 120.1551;
export const DEFAULT_NAME = '杭州';
export const DEFAULT_TIMEZONE = 'Asia/Shanghai';
export const DEFAULT_ELEVATION = 8;

export const BASEMAP_OPTIONS = [
  { value: 'tianditu-vector', label: '天地图矢量' },
  { value: 'tianditu-imagery', label: '天地图影像' },
  { value: 'osm', label: 'OpenStreetMap' },
] as const;

export const CLOUD_FIELD_OPTIONS = [
  { value: 'total', label: '综合云量' },
  { value: 'high_cloud', label: '高云' },
  { value: 'mid_cloud', label: '中云' },
  { value: 'low_cloud', label: '低云' },
] as const;

export const RENDER_MODE_OPTIONS = [
  { value: 'smooth', label: '连续云场' },
  { value: 'grid', label: '原始格点' },
] as const;

export const MODEL_OPTIONS = [
  { value: 'gfs', label: 'GFS' },
  { value: 'ec', label: 'ECMWF' },
] as const;

export const SATELLITE_OPTIONS = [
  { value: 'auto', label: '自动' },
  { value: 'off', label: '关闭' },
] as const;

export const PHENOMENON_OPTIONS = [
  { value: 'dawn', label: '朝霞' },
  { value: 'dusk', label: '晚霞' },
] as const;
