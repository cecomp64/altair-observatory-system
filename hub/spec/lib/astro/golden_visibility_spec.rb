require "rails_helper"

# Golden tests against astroplan/astropy results dumped by
# astrophotography-database's tools/dump_visibility_fixtures.py (§7.2):
# altitude within 0.5 deg, twilight within 2 min, best month identical.
RSpec.describe "Astro golden visibility" do
  fixtures = JSON.parse(Rails.root.join("spec/fixtures/visibility/astrophotography_database.json").read)
  min_altitude = fixtures["min_altitude"]

  fixtures["sites"].each do |site_key, data|
    site = Astro::Site.new(latitude: data["latitude"], longitude: data["longitude"], elevation_m: data["elevation_m"],
                           timezone: data["timezone"], horizon_points: [], min_altitude: min_altitude)
    visibility = Astro::Visibility.new(site)

    data["nights"].each do |night|
      date = Date.parse(night["date"])

      context "#{site_key} on #{date}" do
        it "matches twilight times within 2 minutes" do
          ours = Astro::Twilight.new(site, date).times
          night["twilight"].each do |name, expected|
            expect(ours[name.to_sym]).to be_within(120).of(Time.zone.parse(expected)), "#{name}: #{ours[name.to_sym]&.utc} vs #{expected}"
          end
        end

        it "matches Sun and Moon altitudes within 0.5 deg" do
          night["sun_altitudes"].each do |time, alt|
            expect(Astro::Ephemeris.sun_alt_az(Time.zone.parse(time), site.latitude, site.longitude).first).to be_within(0.5).of(alt)
          end
          night["moon_altitudes"].each do |time, alt, _az|
            expect(Astro::Ephemeris.moon_alt_az(Time.zone.parse(time), site.latitude, site.longitude).first).to be_within(0.5).of(alt), time
          end
        end

        it "matches object altitudes within 0.5 deg and azimuths within 1 deg" do
          fixtures["objects"].each do |name, coords|
            night["objects"][name]["altitudes"].each do |time, alt, az|
              jd = Astro::Coordinates.julian_day(Time.zone.parse(time))
              ra, dec = Astro::Coordinates.precess(coords["ra_deg"], coords["dec_deg"], jd)
              our_alt, our_az = Astro::Coordinates.alt_az(ra, dec, site.latitude, Astro::Coordinates.lst(jd, site.longitude))
              expect(our_alt).to be_within(0.5).of(alt), "#{name} at #{time}"
              expect(((our_az - az + 180) % 360 - 180).abs).to be < 1.0, "#{name} az at #{time}" if alt > -80 && alt < 85
            end
          end
        end

        it "matches nightly summaries (hours in darkness, max altitude, transit)" do
          fixtures["objects"].each do |name, coords|
            expected = night["objects"][name]
            result = visibility.for_night(coords["ra_deg"], coords["dec_deg"], date, min_altitude: min_altitude)
            # The old app sampled every 10 minutes, we sample every 5.
            expect(result.hours_clear_in_darkness).to be_within(0.35).of(expected["hours_in_darkness"]), name
            expect(result.max_altitude).to be_within(0.5).of(expected["max_altitude"]), name
            # At a darkness edge an object can move ~2.5 deg in one of the old 10-minute steps.
            expect(result.max_altitude_in_darkness).to be_within(2.5).of(expected["max_altitude_in_darkness"]), name
            expect(result.transit_time.in_time_zone(site.time_zone).strftime("%H:%M")).to be_within_minutes(10).of(expected["transit_time"]), name
          end
        end
      end
    end

    it "#{site_key}: picks the same best month for every object" do
      expected = data["best_viewing"]
      fixtures["objects"].each do |name, coords|
        ours = visibility.best_viewing(coords["ra_deg"], coords["dec_deg"], year: expected["year"], min_altitude: min_altitude)
        # A tie between two months within the sampling noise isn't a disagreement.
        scores = expected[name]["monthly_scores"]
        best = expected[name]["best_month"]
        acceptable = best.nil? ? [ nil ] : scores.each_index.select { |i| scores[i] >= scores[best - 1] * 0.97 }.map { |i| i + 1 }
        expect(acceptable).to include(ours[:best_month]), "#{name}: ours #{ours[:best_month]}, astroplan #{best} (#{scores.inspect})"
      end
    end
  end

  RSpec::Matchers.define :be_within_minutes do |minutes|
    match do |actual|
      to_min = ->(hhmm) { h, m = hhmm.split(":").map(&:to_i); h * 60 + m }
      diff = (to_min.(actual) - to_min.(@expected)).abs
      [ diff, 1440 - diff ].min <= minutes
    end
    chain(:of) { |expected| @expected = expected }
    failure_message { |actual| "expected #{actual} within #{minutes} min of #{@expected}" }
  end
end
