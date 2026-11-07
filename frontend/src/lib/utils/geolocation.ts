export type GeolocationStatus =
  | 'idle'
  | 'loading'
  | 'success'
  | 'denied'
  | 'unavailable'
  | 'timeout'
  | 'error';

export interface LocalCoordinates {
  lat: number;
  lon: number;
  accuracyM: number | null;
}

export interface GeolocationState {
  status: GeolocationStatus;
  message: string;
}

export interface GeolocationProvider {
  getCurrentPosition: Geolocation['getCurrentPosition'];
}

export class GeolocationRequestError extends Error {
  readonly status: Exclude<GeolocationStatus, 'idle' | 'loading' | 'success'>;

  constructor(
    status: Exclude<GeolocationStatus, 'idle' | 'loading' | 'success'>,
    message: string,
  ) {
    super(message);
    this.name = 'GeolocationRequestError';
    this.status = status;
  }
}

const STATUS_MESSAGES: Record<GeolocationStatus, string> = {
  idle: '',
  loading: '正在获取当前位置…',
  success: '已使用当前位置',
  denied: '浏览器拒绝了定位权限，请在站点设置中允许定位。',
  unavailable: '当前设备无法提供位置，请检查定位服务。',
  timeout: '获取当前位置超时，请重试。',
  error: '获取当前位置失败，请重试。',
};

export function geolocationMessage(status: GeolocationStatus): string {
  return STATUS_MESSAGES[status];
}

export function isValidGeolocationCoordinates(latitude: number, longitude: number): boolean {
  return Number.isFinite(latitude)
    && Number.isFinite(longitude)
    && latitude >= -90
    && latitude <= 90
    && longitude >= -180
    && longitude <= 180;
}

export function browserGeolocation(): GeolocationProvider | null {
  if (typeof navigator === 'undefined' || !navigator.geolocation) return null;
  return navigator.geolocation;
}

export function geolocationErrorStatus(error: unknown): GeolocationRequestError {
  if (error instanceof GeolocationRequestError) return error;

  const code = typeof error === 'object' && error !== null && 'code' in error
    ? Number((error as { code?: unknown }).code)
    : null;
  const status = code === 1
    ? 'denied'
    : code === 2
      ? 'unavailable'
      : code === 3
        ? 'timeout'
        : 'error';
  const suppliedMessage = error instanceof Error ? error.message : '';
  return new GeolocationRequestError(status, suppliedMessage || geolocationMessage(status));
}

export function requestCurrentLocation(
  provider: GeolocationProvider | null = browserGeolocation(),
): Promise<LocalCoordinates> {
  if (!provider) {
    return Promise.reject(new GeolocationRequestError('unavailable', geolocationMessage('unavailable')));
  }

  return new Promise<LocalCoordinates>((resolve, reject) => {
    provider.getCurrentPosition(
      (position) => {
        const latitude = Number(position.coords.latitude);
        const longitude = Number(position.coords.longitude);
        if (!isValidGeolocationCoordinates(latitude, longitude)) {
          reject(new GeolocationRequestError('error', '浏览器返回了无效的位置坐标。'));
          return;
        }
        const accuracy = Number(position.coords.accuracy);
        resolve({
          lat: latitude,
          lon: longitude,
          accuracyM: Number.isFinite(accuracy) && accuracy >= 0 ? accuracy : null,
        });
      },
      (error) => reject(geolocationErrorStatus(error)),
      {
        enableHighAccuracy: true,
        timeout: 10_000,
        maximumAge: 0,
      },
    );
  });
}

export function geolocationState(status: GeolocationStatus, message?: string): GeolocationState {
  return { status, message: message ?? geolocationMessage(status) };
}
