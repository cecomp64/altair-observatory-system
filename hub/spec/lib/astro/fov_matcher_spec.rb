require "rails_helper"

RSpec.describe Astro::FovMatcher do
  let!(:m31) { create(:astro_object, primary_name: "M31", ra_deg: 10.68479, dec_deg: 41.26906) }
  let!(:m32) { create(:astro_object, primary_name: "M32", ra_deg: 10.67430, dec_deg: 40.86517) }
  let!(:m110) { create(:astro_object, primary_name: "M110", ra_deg: 10.09200, dec_deg: 41.68530) }
  let!(:far) { create(:astro_object, primary_name: "M33", ra_deg: 23.46, dec_deg: 30.66) }

  it "finds objects inside the sensor footprint, nearest first" do
    matches = described_class.new(ra: 10.68, dec: 41.27, width: 2.45, height: 1.64).matches
    expect(matches.map { |m| m.object.primary_name }).to eq(%w[M31 M32 M110])
    expect(matches.first.distance_arcmin).to be < 1
  end

  it "respects rotation: a narrow strip along RA misses M32 unless rotated north-south" do
    strip = { ra: 10.68, dec: 41.27, width: 1.5, height: 0.2 }
    expect(described_class.new(**strip).matches.map { |m| m.object.primary_name }).not_to include("M32")
    expect(described_class.new(**strip, rotation: 90).matches.map { |m| m.object.primary_name }).to include("M32")
  end

  it "handles the RA wrap at 0h" do
    wrap = create(:astro_object, primary_name: "Wrap", ra_deg: 359.9, dec_deg: 10)
    expect(described_class.new(ra: 0.1, dec: 10, width: 1, height: 1).matches.map(&:object)).to include(wrap)
  end
end

RSpec.describe FrameFovMatchJob do
  it "links frames to catalogue objects in view, marking the target's object as primary" do
    m31 = create(:astro_object, primary_name: "M31", ra_deg: 10.68479, dec_deg: 41.26906)
    m32 = create(:astro_object, primary_name: "M32", ra_deg: 10.67430, dec_deg: 40.86517)
    target = create(:target, astro_object: m31)
    frames = 2.times.map do |i|
      Frame.create!(sha256: Digest::SHA256.hexdigest("f#{i}"), telescope: target.telescope, optical_train: target.telescope.default_optical_train,
                    target: target, image_type: "light", night: "2026-09-24", date_obs: Time.current, file_name: "f#{i}.fits",
                    logical_path: "raw/f#{i}.fits", ra_deg: 10.68, dec_deg: 41.27, fov_width_deg: 2.45, fov_height_deg: 1.64)
    end

    described_class.perform_now(frames.map(&:id))

    expect(frames.first.frame_objects.find_by!(astro_object: m31).association_type).to eq("primary")
    expect(frames.last.frame_objects.find_by!(astro_object: m32).association_type).to eq("in_fov")
    expect(frames.first.reload.fov_matched_at).to be_present
  end
end
