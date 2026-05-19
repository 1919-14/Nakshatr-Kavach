function asDate(value) {
  const date = value ? new Date(value) : new Date();
  return Number.isNaN(date.getTime()) ? new Date() : date;
}

function formatInIST(value, options) {
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    ...options,
  }).format(asDate(value));
}

export function toIST(utcDateStr) {
  return asDate(utcDateStr);
}

export function formatIST(utcDateStr) {
  return `${formatInIST(utcDateStr, {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  })} IST`;
}

export function formatISTShort(utcDateStr) {
  return `${formatInIST(utcDateStr, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  })} IST`;
}

export function formatISTDate(utcDateStr) {
  return formatInIST(utcDateStr, {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

export function nowIST() {
  return formatISTShort(new Date());
}

export function formatCountdown(totalSeconds) {
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  if (h > 0) return `${String(h).padStart(2,"0")}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
  return `${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
}

export function formatINR(crore) {
  if (crore >= 100) return `Rs ${(crore/100).toFixed(1)}B`;
  return `Rs ${crore}Cr`;
}
