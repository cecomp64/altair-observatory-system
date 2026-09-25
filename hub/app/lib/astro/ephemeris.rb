module Astro
  # Low-precision Sun and Moon (Meeus ch. 25 and 47): the Sun to ~0.01 deg,
  # the Moon to ~0.1 deg with the main periodic terms and topocentric
  # parallax. Plenty for twilight, altitude charts and moon separation.
  module Ephemeris
    DEG = Coordinates::DEG
    EARTH_RADIUS_KM = 6378.14

    # [D, M, M', F, sum_l (1e-6 deg), sum_r (0.001 km)]
    MOON_LR = [
      [ 0, 0, 1, 0, 6_288_774, -20_905_355 ], [ 2, 0, -1, 0, 1_274_027, -3_699_111 ], [ 2, 0, 0, 0, 658_314, -2_955_968 ],
      [ 0, 0, 2, 0, 213_618, -569_925 ], [ 0, 1, 0, 0, -185_116, 48_888 ], [ 0, 0, 0, 2, -114_332, -3_149 ],
      [ 2, 0, -2, 0, 58_793, 246_158 ], [ 2, -1, -1, 0, 57_066, -152_138 ], [ 2, 0, 1, 0, 53_322, -170_733 ],
      [ 2, -1, 0, 0, 45_758, -204_586 ], [ 0, 1, -1, 0, -40_923, -129_620 ], [ 1, 0, 0, 0, -34_720, 108_743 ],
      [ 0, 1, 1, 0, -30_383, 104_755 ], [ 2, 0, 0, -2, 15_327, 10_321 ], [ 0, 0, 1, 2, -12_528, 0 ],
      [ 0, 0, 1, -2, 10_980, 79_661 ], [ 4, 0, -1, 0, 10_675, -34_782 ], [ 0, 0, 3, 0, 10_034, -23_210 ],
      [ 4, 0, -2, 0, 8_548, -21_636 ], [ 2, 1, -1, 0, -7_888, 24_208 ], [ 2, 1, 0, 0, -6_766, 30_824 ],
      [ 1, 0, -1, 0, -5_163, -8_379 ], [ 1, 1, 0, 0, 4_987, -16_675 ], [ 2, -1, 1, 0, 4_036, -12_831 ],
      [ 2, 0, 2, 0, 3_994, -10_445 ], [ 4, 0, 0, 0, 3_861, -11_650 ], [ 2, 0, -3, 0, 3_665, 14_403 ],
      [ 0, 1, -2, 0, -2_689, -7_003 ], [ 2, 0, -1, 2, -2_602, 0 ], [ 2, -1, -2, 0, 2_390, 10_056 ],
      [ 1, 0, 1, 0, -2_348, 6_322 ], [ 2, -2, 0, 0, 2_236, -9_884 ]
    ].freeze
    # [D, M, M', F, sum_b (1e-6 deg)]
    MOON_B = [
      [ 0, 0, 0, 1, 5_128_122 ], [ 0, 0, 1, 1, 280_602 ], [ 0, 0, 1, -1, 277_693 ], [ 2, 0, 0, -1, 173_237 ],
      [ 2, 0, -1, 1, 55_413 ], [ 2, 0, -1, -1, 46_271 ], [ 2, 0, 0, 1, 32_573 ], [ 0, 0, 2, 1, 17_198 ],
      [ 2, 0, 1, -1, 9_266 ], [ 0, 0, 2, -1, 8_822 ], [ 2, -1, 0, -1, 8_216 ], [ 2, 0, -2, -1, 4_324 ],
      [ 2, 0, 1, 1, 4_200 ], [ 2, 1, 0, -1, -3_359 ], [ 2, -1, -1, 1, 2_463 ], [ 2, -1, 0, 1, 2_211 ],
      [ 2, -1, -1, -1, 2_065 ], [ 0, 1, -1, -1, -1_870 ], [ 4, 0, -1, -1, 1_828 ], [ 0, 1, 0, 1, -1_794 ]
    ].freeze

    module_function

    def obliquity(t, omega)
      23.439291 - 0.0130042 * t + 0.00256 * Math.cos(omega * DEG)
    end

    # Apparent geocentric Sun: [ra, dec, ecliptic longitude] of date.
    def sun(jd)
      t = Coordinates.centuries(jd)
      l0 = 280.46646 + 36_000.76983 * t + 0.0003032 * t**2
      m = (357.52911 + 35_999.05029 * t - 0.0001537 * t**2) * DEG
      c = (1.914602 - 0.004817 * t - 0.000014 * t**2) * Math.sin(m) + (0.019993 - 0.000101 * t) * Math.sin(2 * m) + 0.000289 * Math.sin(3 * m)
      omega = 125.04 - 1934.136 * t
      lambda = (l0 + c - 0.00569 - 0.00478 * Math.sin(omega * DEG)) % 360
      eps = obliquity(t, omega) * DEG
      ra = Math.atan2(Math.cos(eps) * Math.sin(lambda * DEG), Math.cos(lambda * DEG)) / DEG
      dec = Math.asin(Math.sin(eps) * Math.sin(lambda * DEG)) / DEG
      [ ra % 360, dec, lambda ]
    end

    # Geocentric Moon: [ra, dec, distance_km, ecliptic longitude, latitude] of date.
    def moon(jd)
      t = Coordinates.centuries(jd)
      lp = 218.3164477 + 481_267.88123421 * t - 0.0015786 * t**2
      d = 297.8501921 + 445_267.1114034 * t - 0.0018819 * t**2
      m = 357.5291092 + 35_999.0502909 * t - 0.0001536 * t**2
      mp = 134.9633964 + 477_198.8675055 * t + 0.0087414 * t**2
      f = 93.2720950 + 483_202.0175233 * t - 0.0036539 * t**2
      e = 1 - 0.002516 * t - 0.0000074 * t**2
      a1 = 119.75 + 131.849 * t
      a2 = 53.09 + 479_264.290 * t
      a3 = 313.45 + 481_266.484 * t

      sl = sr = sb = 0.0
      MOON_LR.each do |cd, cm, cmp, cf, l, r|
        arg = (cd * d + cm * m + cmp * mp + cf * f) * DEG
        factor = e**cm.abs
        sl += l * factor * Math.sin(arg)
        sr += r * factor * Math.cos(arg)
      end
      MOON_B.each do |cd, cm, cmp, cf, b|
        sb += b * e**cm.abs * Math.sin((cd * d + cm * m + cmp * mp + cf * f) * DEG)
      end
      sl += 3958 * Math.sin(a1 * DEG) + 1962 * Math.sin((lp - f) * DEG) + 318 * Math.sin(a2 * DEG)
      sb += -2235 * Math.sin(lp * DEG) + 382 * Math.sin(a3 * DEG) + 175 * Math.sin((a1 - f) * DEG) +
            175 * Math.sin((a1 + f) * DEG) + 127 * Math.sin((lp - mp) * DEG) - 115 * Math.sin((lp + mp) * DEG)

      omega = 125.04452 - 1934.136261 * t
      lambda = (lp + sl / 1e6 - 0.004778 * Math.sin(omega * DEG)) % 360 # + nutation in longitude
      beta = sb / 1e6
      distance = 385_000.56 + sr / 1000.0
      eps = obliquity(t, omega) * DEG
      lr = lambda * DEG
      br = beta * DEG
      ra = Math.atan2(Math.sin(lr) * Math.cos(eps) - Math.tan(br) * Math.sin(eps), Math.cos(lr)) / DEG
      dec = Math.asin(Math.sin(br) * Math.cos(eps) + Math.cos(br) * Math.sin(eps) * Math.sin(lr)) / DEG
      [ ra % 360, dec, distance, lambda, beta ]
    end

    # Sun [altitude, azimuth] for a site at a moment.
    def sun_alt_az(time, latitude, longitude)
      jd = Coordinates.julian_day(time)
      ra, dec, = sun(jd)
      Coordinates.alt_az(ra, dec, latitude, Coordinates.lst(jd, longitude))
    end

    # Topocentric Moon [altitude, azimuth] (parallax applied).
    def moon_alt_az(time, latitude, longitude)
      jd = Coordinates.julian_day(time)
      ra, dec, distance = moon(jd)
      alt, az = Coordinates.alt_az(ra, dec, latitude, Coordinates.lst(jd, longitude))
      parallax = Math.asin(EARTH_RADIUS_KM / distance) / DEG
      [ alt - parallax * Math.cos(alt * DEG), az ]
    end

    # Illuminated fraction of the Moon (0..1).
    def moon_illumination(time)
      jd = Coordinates.julian_day(time)
      _, _, sun_lambda = sun(jd)
      _, _, distance, lambda, beta = moon(jd)
      elongation = Math.acos((Math.cos(beta * DEG) * Math.cos((lambda - sun_lambda) * DEG)).clamp(-1.0, 1.0))
      sun_distance = 149_597_870.7
      phase = Math.atan2(sun_distance * Math.sin(elongation), distance - sun_distance * Math.cos(elongation))
      (1 + Math.cos(phase)) / 2.0
    end

    # J2000-ish RA/Dec of the Moon (of date is close enough for separations).
    def moon_ra_dec(time)
      ra, dec, = moon(Coordinates.julian_day(time))
      [ ra, dec ]
    end
  end
end
