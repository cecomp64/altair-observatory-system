module Astro
  # Time scales, sidereal time, precession and horizontal coordinates
  # (Meeus, Astronomical Algorithms, ch. 12, 13, 21). Angles in degrees.
  module Coordinates
    DEG = Math::PI / 180.0
    J2000 = 2_451_545.0

    module_function

    def julian_day(time)
      time.to_f / 86_400.0 + 2_440_587.5
    end

    def centuries(jd)
      (jd - J2000) / 36_525.0
    end

    # Greenwich mean sidereal time (degrees), UTC standing in for UT1.
    def gmst(jd)
      t = centuries(jd)
      (280.46061837 + 360.98564736629 * (jd - J2000) + 0.000387933 * t**2 - t**3 / 38_710_000.0) % 360
    end

    def lst(jd, longitude)
      (gmst(jd) + longitude) % 360
    end

    # J2000 (ICRS) RA/Dec to mean equator and equinox of date.
    def precess(ra, dec, jd)
      t = centuries(jd)
      zeta = (2306.2181 * t + 0.30188 * t**2 + 0.017998 * t**3) / 3600.0 * DEG
      z = (2306.2181 * t + 1.09468 * t**2 + 0.018203 * t**3) / 3600.0 * DEG
      theta = (2004.3109 * t - 0.42665 * t**2 - 0.041833 * t**3) / 3600.0 * DEG
      a0 = ra * DEG
      d0 = dec * DEG
      a = Math.cos(d0) * Math.sin(a0 + zeta)
      b = Math.cos(theta) * Math.cos(d0) * Math.cos(a0 + zeta) - Math.sin(theta) * Math.sin(d0)
      c = Math.sin(theta) * Math.cos(d0) * Math.cos(a0 + zeta) + Math.cos(theta) * Math.sin(d0)
      [ ((Math.atan2(a, b) + z) / DEG) % 360, Math.asin(c.clamp(-1.0, 1.0)) / DEG ]
    end

    # Equatorial (of date) to [altitude, azimuth]; azimuth from north through east.
    def alt_az(ra, dec, latitude, lst_deg)
      h = (lst_deg - ra) * DEG
      phi = latitude * DEG
      d = dec * DEG
      sin_alt = Math.sin(phi) * Math.sin(d) + Math.cos(phi) * Math.cos(d) * Math.cos(h)
      alt = Math.asin(sin_alt.clamp(-1.0, 1.0))
      az = Math.atan2(-Math.cos(d) * Math.sin(h), Math.sin(d) * Math.cos(phi) - Math.cos(d) * Math.cos(h) * Math.sin(phi))
      [ alt / DEG, (az / DEG) % 360 ]
    end

    # Great-circle separation between two RA/Dec points (degrees).
    def separation(ra1, dec1, ra2, dec2)
      d1 = dec1 * DEG
      d2 = dec2 * DEG
      dra = (ra2 - ra1) * DEG
      a = Math.sin((d2 - d1) / 2)**2 + Math.cos(d1) * Math.cos(d2) * Math.sin(dra / 2)**2
      2 * Math.asin(Math.sqrt(a.clamp(0.0, 1.0))) / DEG
    end

    # Hour angle (hours, -12..12) of a J2000 object at a moment.
    def hour_angle(ra, dec, longitude, time)
      jd = julian_day(time)
      ra_date, = precess(ra, dec, jd)
      ((lst(jd, longitude) - ra_date + 540) % 360 - 180) / 15.0
    end
  end
end
