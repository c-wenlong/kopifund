import { describe, expect, it } from "vitest";
import {
  addDays, ago, formatDate, formatDateTime, formatStampDay, formatDay, formatDayShort, formatMonth, formatRange, formatTime, formatWeekday, isoDay, isoMonth, isoParts, parseDay, parseMonth,
  shiftMonth, toLocalDate, toMillis,
} from "./dates";
import { tickLabel } from "./stats/hint";
import { zoneSwitch } from "./test-zone";

describe("a phone's short day and how long ago", () => {
  it("drops the year from a day, and leaves other text alone", () => {
    expect(formatDayShort("2026-10-03")).toBe("3 Oct");
    expect(formatDayShort("2026-10")).toBe("2026-10");
    expect(formatDayShort("soon")).toBe("soon");
  });

  it("says how long ago in days, then months, then years", () => {
    const asOf = "2026-09-20";
    expect(ago("2026-09-20", asOf)).toBe("today");
    expect(ago("2026-09-30", asOf)).toBe("today");
    expect(ago("2026-09-19", asOf)).toBe("yesterday");
    expect(ago("2026-09-01", asOf)).toBe("19 days ago");
    expect(ago("2026-08-20", asOf)).toBe("1 month ago");
    expect(ago("2026-08-21", asOf)).toBe("30 days ago");
    expect(ago("2025-10-15", asOf)).toBe("11 months ago");
    expect(ago("2024-09-20", asOf)).toBe("2 years ago");
    expect(ago("2022-01-05", asOf)).toBe("4 years ago");
  });

  it("is empty for text that is not a day", () => {
    expect(ago("", "2026-09-20")).toBe("");
    expect(ago("2026-09", "2026-09-20")).toBe("");
    expect(ago("2026-09-01", "later")).toBe("");
  });
});

describe("one date format", () => {
  it("shows a day without a leading zero, a short month and the whole year", () => {
    expect(formatDay("2026-10-03")).toBe("3 Oct 2026");
    expect(formatDay("2026-01-31")).toBe("31 Jan 2026");
  });

  it("shows a month as the short month and the whole year", () => {
    expect(formatMonth("2026-10")).toBe("Oct 2026");
    expect(formatMonth("2026-10-03")).toBe("Oct 2026");
  });

  it("formats whichever of a day or a month it is given, and leaves other text alone", () => {
    expect(formatDate("2026-10-03")).toBe("3 Oct 2026");
    expect(formatDate("2026-10")).toBe("Oct 2026");
    expect(formatDate("Grab")).toBe("Grab");
    expect(formatDate("2026-13")).toBe("2026-13");
    expect(formatDate(null)).toBe("");
    expect(formatDate(42)).toBe("42");
    expect(formatDay("not a date")).toBe("not a date");
  });

  it("names the year once in a range inside one year, and both years across two", () => {
    expect(formatRange("2026-10-03", "2026-10-09")).toBe("3 Oct to 9 Oct 2026");
    expect(formatRange("2025-12-28", "2026-01-03")).toBe("28 Dec 2025 to 3 Jan 2026");
    expect(formatRange("2026-10", "2026-12")).toBe("Oct to Dec 2026");
    expect(formatRange("2025-11", "2026-02")).toBe("Nov 2025 to Feb 2026");
    expect(formatRange("2026-10-03", "2026-10-03")).toBe("3 Oct 2026");
  });

  it("reads a date from its parts, so no time zone moves it to the day before", () => {
    // new Date("2026-10-03") is UTC midnight, which is 2 October west of Greenwich
    expect(isoParts("2026-10-03")).toEqual({ y: 2026, m: 10, d: 3 });
    expect(formatDay("2026-10-01")).toBe("1 Oct 2026");
  });

  it("gives this device's calendar day and month, not UTC's", () => {
    const early = new Date(2026, 9, 1, 0, 30);   // 00:30 local on 1 Oct
    expect(isoDay(early)).toBe("2026-10-01");
    expect(isoMonth(early)).toBe("2026-10");
  });

  describe("a server's time stamp", () => {
    it("shows the day and a 24-hour time on this device", () => {
      const stamp = "2026-10-12T14:05:09";   // no offset: read as this device's time
      expect(formatDateTime(stamp)).toBe("12 Oct 2026, 14:05");
      expect(formatTime(stamp)).toBe("14:05:09");
    });

    it("reads a stamp to the microsecond, as the upload history and loads now keep them (G18)", () => {
      // a browser is only bound to read three digits of a second; the rest are dropped first
      expect(toMillis("2026-10-12T14:05:09.123456+00:00")).toBe("2026-10-12T14:05:09.123+00:00");
      expect(toMillis("2026-10-12T14:05:09+00:00")).toBe("2026-10-12T14:05:09+00:00");
      expect(formatTime("2026-10-12T14:05:09.123456")).toBe("14:05:09");
      expect(formatDateTime("2026-10-12T14:05:09.123456")).toBe("12 Oct 2026, 14:05");
    });

    it("leaves text that is not a time alone", () => {
      expect(formatDateTime("yesterday")).toBe("yesterday");
      expect(formatTime("2026-10-12")).toBe("2026-10-12");
    });
  });

  it("keeps the compact form for chart ticks only", () => {
    expect(tickLabel("2025-10")).toBe("Oct 25");
    expect(tickLabel("2024-07-01")).toBe("1 Jul 24");
    expect(tickLabel("2025-10", true)).toBe("Oct 2025");
    expect(tickLabel("2024-07-01", true)).toBe("1 Jul 2024");
  });

  describe("reading what a person types", () => {
    it("reads a day written the way it is shown, in words or numbers", () => {
      for (const text of ["3 Oct 2026", "3 oct 2026", "3 October 2026", "  3  OCT  2026 ", "3-Oct-2026", "3oct2026", "Oct 3 2026",
        "Oct 3, 2026", "october 3rd 2026", "3rd Oct 2026", "2026-10-03", "2026/10/3", "2026.10.03", "3/10/2026", "03-10-2026", "3.10.2026"]) {
        expect(parseDay(text), text).toBe("2026-10-03");
      }
      expect(parseDay("1 Sept 2026")).toBe("2026-09-01");
    });

    it("reads 3/10/2026 day first, as it is written here", () => {
      expect(parseDay("3/10/2026")).toBe("2026-10-03");
      expect(parseDay("10/3/2026")).toBe("2026-03-10");
      expect(parseDay("31/12/1999")).toBe("1999-12-31");
    });

    it("refuses what is not a real day, a two-digit year, or half a date", () => {
      for (const text of ["", "  ", "soon", "3 Oct", "3 Oct 26", "3/10/26", "31 Feb 2026", "29 Feb 2025", "0 Oct 2026", "32 Oct 2026",
        "3 Foo 2026", "3 Oc 2026", "2026-13-01", "2026-10-32", "2026-10", "99999-10-03"]) {
        expect(parseDay(text), text).toBeNull();
      }
      expect(parseDay("29 Feb 2028")).toBe("2028-02-29");
    });

    it("reads a month in words or numbers", () => {
      for (const text of ["Oct 2026", "oct 2026", "October 2026", "oct-2026", "2026-10", "2026/10", "10/2026", "10 2026", "2026 oct"]) {
        expect(parseMonth(text), text).toBe("2026-10");
      }
      expect(parseMonth("sept 2026")).toBe("2026-09");
      for (const text of ["", "Oct", "2026", "13/2026", "2026-00", "Foo 2026", "3 Oct 2026"]) expect(parseMonth(text), text).toBeNull();
    });

    it("round-trips what the app shows", () => {
      for (const iso of ["2026-01-31", "2024-02-29", "1995-07-04"]) expect(parseDay(formatDay(iso))).toBe(iso);
      for (const iso of ["2026-01", "1999-12"]) expect(parseMonth(formatMonth(iso))).toBe(iso);
    });
  });

  describe("calendar arithmetic on ISO values", () => {
    it("adds days across a month and a year, and refuses a month", () => {
      expect(addDays("2026-10-31", 1)).toBe("2026-11-01");
      expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
      expect(addDays("2026-03-01", -1)).toBe("2026-02-28");
      expect(addDays("2026-10", 1)).toBeNull();
      expect(addDays("soon", 1)).toBeNull();
    });

    it("steps a month back and forward across a year", () => {
      expect(shiftMonth("2026-01", -1)).toBe("2025-12");
      expect(shiftMonth("2026-12", 1)).toBe("2027-01");
      expect(shiftMonth("2026-10-17", 2)).toBe("2026-12");
      expect(shiftMonth("2026-10", -12)).toBe("2025-10");
      expect(shiftMonth("nope", 1)).toBeNull();
    });

    it("builds a local date from the parts, never through UTC", () => {
      const d = toLocalDate("2026-10-03")!;
      expect([d.getFullYear(), d.getMonth(), d.getDate()]).toEqual([2026, 9, 3]);
      expect(toLocalDate("2026-10")!.getDate()).toBe(1);
      expect(toLocalDate("nope")).toBeNull();
    });
  });
});

describe("formatWeekday", () => {
  it("puts the weekday before the day, worked out from the date's own parts", () => {
    expect(formatWeekday("2026-10-13")).toBe("Tue 13 Oct 2026");
    expect(formatWeekday("2026-01-01")).toBe("Thu 1 Jan 2026");
    expect(formatWeekday("2028-02-29")).toBe("Tue 29 Feb 2028");
  });

  it("hands back anything that is not a day", () => {
    expect(formatWeekday("2026-10")).toBe("2026-10");
    expect(formatWeekday("soon")).toBe("soon");
  });
});

// Audit: the device's zone matters to what a stamp reads and to nothing else
describe("time zones", () => {
  const inZone = zoneSwitch();

  it("dates a stamp in this device's time, so a Singapore morning is that day", () => {
    inZone("Asia/Singapore");
    expect(formatStampDay("2026-10-03T22:30:00+00:00")).toBe("4 Oct 2026");
    inZone("America/Los_Angeles");
    expect(formatStampDay("2026-10-03T22:30:00+00:00")).toBe("3 Oct 2026");
    expect(formatStampDay("not a stamp")).toBe("not a stamp");
  });

  it("reads and steps days the same in a zone that skipped one", () => {
    // Kiritimati went from 30 Dec 1994 to 1 Jan 1995 at local midnight
    inZone("Pacific/Kiritimati");
    expect(parseDay("31 Dec 1994")).toBe("1994-12-31");
    expect(addDays("1994-12-30", 1)).toBe("1994-12-31");
    expect(addDays("1995-01-01", -1)).toBe("1994-12-31");
    inZone("Pacific/Apia");
    expect(parseDay("30 Dec 2011")).toBe("2011-12-30");
    expect(addDays("2011-12-29", 2)).toBe("2011-12-31");
  });

  it("still steps over a month end and a leap day", () => {
    expect(addDays("2024-02-28", 1)).toBe("2024-02-29");
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
    expect(addDays("2026-03-01", -1)).toBe("2026-02-28");
    expect(parseDay("29 Feb 2025")).toBeNull();
  });
});
