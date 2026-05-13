import { format, addMinutes } from "date-fns";

// Always display IST = UTC + 5:30
export function toIST(utcDateStr) {
  const d = new Date(utcDateStr);
  const ist = addMinutes(d, 330);
  return ist;
}

export function formatIST(utcDateStr, fmt = "dd MMM yyyy HH:mm:ss") {
  return format(toIST(utcDateStr), fmt) + " IST";
}

export function formatISTShort(utcDateStr) {
  return format(toIST(utcDateStr), "HH:mm:ss") + " IST";
}

export function formatISTDate(utcDateStr) {
  return format(toIST(utcDateStr), "dd MMM yyyy");
}

export function nowIST() {
  return format(addMinutes(new Date(), 330), "HH:mm:ss") + " IST";
}

export function formatCountdown(totalSeconds) {
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  if (h > 0) return `${String(h).padStart(2,"0")}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
  return `${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
}

export function formatINR(crore) {
  if (crore >= 100) return `₹${(crore/100).toFixed(1)}B`;
  return `₹${crore}Cr`;
}
