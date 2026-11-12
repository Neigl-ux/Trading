// Datumsbeschriftungen für den Chart. Die Zeitstempel im Chart sind bereits
// auf deutsche Zeit verschoben (siehe app.js), deshalb wird hier in "UTC"
// formatiert, damit nichts doppelt verschoben wird.

const fmt = (opts) => new Intl.DateTimeFormat("de-DE", { ...opts, timeZone: "UTC" });
const F = {
  year: fmt({ year: "numeric" }),
  month: fmt({ month: "short" }),
  monthYear: fmt({ month: "short", year: "numeric" }),
  monthLong: fmt({ month: "long", year: "numeric" }),
  day: fmt({ day: "2-digit", month: "2-digit" }),
  date: fmt({ day: "2-digit", month: "2-digit", year: "numeric" }),
  weekdayDate: fmt({ weekday: "short", day: "2-digit", month: "2-digit", year: "numeric" }),
  time: fmt({ hour: "2-digit", minute: "2-digit" }),
};

// Typen der Achsenbeschriftung wie in Lightweight Charts (TickMarkType)
export const TICK = { Year: 0, Month: 1, DayOfMonth: 2, Time: 3, TimeWithSeconds: 4 };

const d = (sec) => new Date(sec * 1000);

/** Beschriftung der Zeitachse: je weiter herausgezoomt, desto gröber. */
export function tickLabel(sec, type) {
  switch (type) {
    case TICK.Year:
      return F.year.format(d(sec));
    case TICK.Month: {
      // Januar bekommt die Jahreszahl, damit man beim Scrollen die Orientierung behält
      const date = d(sec);
      return date.getUTCMonth() === 0 ? F.monthYear.format(date) : F.month.format(date);
    }
    case TICK.DayOfMonth:
      return F.day.format(d(sec));
    default: {
      // Uhrzeit; um Mitternacht stattdessen das Datum, damit Tageswechsel sichtbar sind
      const date = d(sec);
      return date.getUTCHours() === 0 && date.getUTCMinutes() === 0 ? F.day.format(date) : F.time.format(date);
    }
  }
}

/** Ausführliches Datum für Fadenkreuz und Infozeile, passend zur Kerzenlänge. */
export function candleLabel(sec, interval) {
  const date = d(sec);
  switch (interval) {
    case "1M":
      return F.monthLong.format(date);
    case "1w":
      return `Woche ab ${F.date.format(date)}`;
    case "2w":
      return `2 Wochen ab ${F.date.format(date)}`;
    case "1d":
      return F.weekdayDate.format(date);
    default:
      return `${F.weekdayDate.format(date)}, ${F.time.format(date)} Uhr`;
  }
}
