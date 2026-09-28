require "rails_helper"

RSpec.describe AstroChartsHelper, type: :helper do
  let(:telescope) { create(:telescope, min_altitude_deg: 25) }
  let(:site) { Astro::Site.for(telescope) }
  let(:date) { Date.new(2026, 1, 15) }
  let(:visibility) { Astro::Visibility.new(site) }
  let(:night) { visibility.night(date) }

  def datasets(html)
    JSON.parse(Nokogiri::HTML(html).at_css("canvas")["data-chart-data-value"])["datasets"]
  end

  describe "#horizon_chart" do
    it "draws only the minimum altitude when there is no horizon file" do
      expect(datasets(helper.horizon_chart(site)).map { |d| d["label"] }).to eq([ "Minimum altitude (25.0°)" ])
    end

    it "draws a path only while it is dark and the object is up, split where it wraps past north" do
      # Near the pole the object circles north, crossing azimuth 0/360.
      result = visibility.for_night(40.0, 80.0, date)
      path = datasets(helper.horizon_chart(site, night: night, paths: [ { label: "Near the pole", result: result } ])).last
      shown = path["data"].reject { |p| p["y"].nil? }

      expect(shown).not_to be_empty
      expect(shown).to all(include("tip" => a_string_starting_with("Near the pole · ")))
      expect(shown.pluck("y")).to all(be >= 0)
      expect(shown.pluck("x")).to include(be < 20, be > 340)
      path["data"].each_cons(2) do |a, b|
        next if a["y"].nil? || b["y"].nil?

        expect((a["x"] - b["x"]).abs).to be < 180
      end

      # Exactly the samples in astronomical darkness, with a dot on each hour.
      zone = site.time_zone
      expected = result.times.each_index.select { |i| night.dark_flags[i] && result.altitudes[i] >= 0 }
      expect(shown.map { |p| p["tip"][/\d\d:\d\d/] }).to eq(expected.map { |i| result.times[i].in_time_zone(zone).strftime("%H:%M") })
      dots = path["data"].zip(path["pointRadius"]).select { |_, r| r.positive? }.map { |p, _| p["tip"][/\d\d:\d\d/] }
      expect(dots).not_to be_empty
      expect(dots).to all(end_with(":00"))
    end

    it "leaves out an object that never rises" do
      result = visibility.for_night(0.0, -80.0, date)
      path = datasets(helper.horizon_chart(site, night: night, paths: [ { label: "South", result: result } ])).last
      expect(path["data"]).to be_empty
    end
  end

  describe "#horizon_silhouette" do
    it "shows a flat horizon and the minimum altitude without a horizon file" do
      html = helper.horizon_silhouette(telescope)
      expect(html).to include("Flat horizon", "25° minimum")
      expect(Nokogiri::HTML(html).css("polygon")).to be_empty
    end

    it "draws the horizon mask and its range" do
      telescope.update!(horizon_file: Rack::Test::UploadedFile.new(StringIO.new("0,5\n180,35\n"), "text/csv", original_filename: "h.csv"))
      html = helper.horizon_silhouette(telescope)
      expect(html).to include("Horizon 5°–35°")
      expect(Nokogiri::HTML(html).at_css("svg")["aria-label"]).to eq("Horizon 5°–35°, minimum altitude 25°, by azimuth from north")
      expect(Nokogiri::HTML(html).css("polygon").size).to eq(1)
    end
  end
end
