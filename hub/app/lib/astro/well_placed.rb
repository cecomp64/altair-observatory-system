module Astro
  # Catalogue objects well placed tonight from a telescope: clear of its
  # horizon for at least an hour of astronomical darkness. Evaluated on a
  # coarser 15-minute grid for the whole catalogue, then cached for an hour.
  class WellPlaced
    STEP_MINUTES = 15
    Entry = Struct.new(:id, :hours, :max_altitude, :score, keyword_init: true)

    def self.for(telescope, date: telescope.night_for(Time.current))
      site = Site.for(telescope)
      Rails.cache.fetch([ "astro/well-placed/v1", site.cache_key, date ], expires_in: 1.hour) do
        new(site, date).compute(AstroObject.where.not(ra_deg: nil).where.not(dec_deg: nil))
      end
    end

    def initialize(site, date)
      @site = site
      @night = Night.new(site, date, step_minutes: STEP_MINUTES)
      @horizon = site.horizon
    end

    # { astro_object_id => Entry } for every object clear >= 1 h in darkness.
    def compute(scope)
      dark = @night.dark_flags
      return {} unless dark.any?

      min_dec, max_dec = Visibility.new(@site).declination_bounds
      step = @night.step_hours
      scope.where(dec_deg: min_dec..max_dec).pluck(:id, :ra_deg, :dec_deg).each_with_object({}) do |(id, ra, dec), out|
        track = @night.track(ra, dec)
        clear = 0
        max_alt = 0.0
        track.each_with_index do |(alt, az), i|
          next unless dark[i]

          max_alt = alt if alt > max_alt
          clear += 1 if alt >= @horizon.effective_min_altitude(az)
        end
        hours = clear * step
        next if hours < 1.0

        out[id] = Entry.new(id: id, hours: hours.round(2), max_altitude: max_alt.round(1),
                            score: (max_alt * 0.5 + hours * 10 + 30).round(1))
      end
    end
  end
end
