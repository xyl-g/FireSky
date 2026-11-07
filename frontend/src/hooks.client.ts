import type { HandleClientError } from '@sveltejs/kit';

export const handleError: HandleClientError = async ({ error, event, status, message }) => {
  console.error('Client error:', error);
  return {
    message: message || '发生意外错误',
  };
};
