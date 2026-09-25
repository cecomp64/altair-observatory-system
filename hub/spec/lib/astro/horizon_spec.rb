require "rails_helper"

RSpec.describe Astro::Horizon do
  subject(:horizon) { described_class.new([ [ 0, 10 ], [ 90, 30 ], [ 180, 20 ], [ 270, 40 ] ], min_altitude: 25) }

  it "interpolates between points and wraps around north" do
    expect(horizon.mask_altitude(45)).to eq(20)
    expect(horizon.mask_altitude(315)).to eq(25)
    expect(horizon.mask_altitude(360)).to eq(10)
  end

  it "uses the higher of the mask and the minimum altitude" do
    expect(horizon.effective_min_altitude(0)).to eq(25)
    expect(horizon.effective_min_altitude(270)).to eq(40)
    expect(horizon.clear?(35, 270)).to be(false)
    expect(horizon.clear?(35, 0)).to be(true)
  end

  it "falls back to the minimum altitude without a mask" do
    expect(described_class.new([], min_altitude: 30).effective_min_altitude(123)).to eq(30)
  end
end

RSpec.describe Astro::Visibility do
  let(:site) { Astro::Site.new(latitude: 37.34, longitude: -121.89, elevation_m: 100, timezone: "America/Los_Angeles", horizon_points: [], min_altitude: 30) }

  it "loses clear hours behind a horizon mask" do
    masked = site.dup.tap { |s| s.horizon_points = [ [ 0, 70 ], [ 359, 70 ] ] }
    open_sky = described_class.new(site).for_night(10.6847, 41.269, Date.new(2026, 9, 24))
    walled = described_class.new(masked).for_night(10.6847, 41.269, Date.new(2026, 9, 24))

    expect(open_sky.hours_clear_in_darkness).to be > 6
    expect(walled.hours_clear_in_darkness).to be < open_sky.hours_clear_in_darkness
  end

  it "scores only objects visible for an hour or more" do
    m31 = described_class.new(site).for_night(10.6847, 41.269, Date.new(2026, 9, 24))
    lmc = described_class.new(site).for_night(80.89, -69.76, Date.new(2026, 9, 24))
    expect(described_class.imaging_score(m31, progress: 50, priority: 2)).to be > 100
    expect(described_class.imaging_score(lmc)).to eq(0)
  end

  it "lists well-placed catalogue objects" do
    telescope = create(:telescope, latitude: 37.34, longitude: -121.89, timezone: "America/Los_Angeles")
    m31 = create(:astro_object, ra_deg: 10.6847, dec_deg: 41.269)
    lmc = create(:astro_object, ra_deg: 80.89, dec_deg: -69.76)
    list = Astro::WellPlaced.for(telescope, date: Date.new(2026, 9, 24))
    expect(list.keys).to include(m31.id)
    expect(list.keys).not_to include(lmc.id)
  end
end
