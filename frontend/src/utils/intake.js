export const CHUNK_SIZE = 512 * 1024;
const STORAGE_KEY = 'cold-excursion.intake.receipt';
const LAST_KEY = 'cold-excursion.intake.last';

export const formatBytes = value => {
  const n = Number(value) || 0;
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
  return (n / 1024 / 1024 / 1024).toFixed(2) + ' GB';
};

export const totalChunks = (size, chunk = CHUNK_SIZE) => Math.ceil(size / chunk);

export const segmentCount = (session, index) => {
  if (!session) return 0;
  if (index === session.total_chunks - 1) {
    return session.total_bytes - session.chunk_size * (session.total_chunks - 1);
  }
  return session.chunk_size;
};

// 接收单是续传凭证：页面关闭后仍可在同一浏览器找回，也可手动输入。
export const rememberReceipt = receipt => {
  try { localStorage.setItem(STORAGE_KEY, receipt); } catch (_) { /* 隐私模式下仅本次有效 */ }
};
export const forgetReceipt = () => {
  try { localStorage.removeItem(STORAGE_KEY); } catch (_) { /* 忽略 */ }
};
export const rememberedReceipt = () => {
  try { return localStorage.getItem(STORAGE_KEY) || ''; } catch (_) { return ''; }
};
export const rememberLast = value => {
  try { localStorage.setItem(LAST_KEY, JSON.stringify(value)); } catch (_) { /* 忽略 */ }
};
export const lastResult = () => {
  try { return JSON.parse(localStorage.getItem(LAST_KEY) || 'null'); } catch (_) { return null; }
};

export function describeFailure(session) {
  if (!session) return '';
  if (session.status === 'published') return '';
  if (session.status === 'failed' && session.failure_reason) return session.failure_reason;
  if (session.missing_count) return '尚缺 ' + session.missing_count + ' 个分段，已确认位置可继续补传。';
  return '';
}
