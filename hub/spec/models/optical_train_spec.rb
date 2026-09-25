require "rails_helper"

RSpec.describe OpticalTrain, type: :model do
  subject(:train) { build(:optical_train) }

  it { is_expected.to belong_to(:telescope) }
  it { is_expected.to validate_presence_of(:name) }

  it "requires a rig-style key" do
    train.key = "Esprit 100!"
    expect(train).not_to be_valid
  end

  it "maps raw FILTER values to canonical names, names first then aliases, ignoring case" do
    expect(train.canonical_filter("ha")).to eq("Ha")
    expect(train.canonical_filter("H-alpha")).to eq("Ha")
    expect(train.canonical_filter("luminance")).to eq("L")
    expect(train.canonical_filter("SII")).to be_nil
  end

  it "computes pixel scale and field of view" do
    expect(train.pixel_scale_arcsec).to be_within(0.001).of(1.410)
    width, height = train.fov_deg
    expect(width).to be_within(0.01).of(2.45)
    expect(height).to be_within(0.01).of(1.64)
  end

  it "round-trips the filter list through the admin text form" do
    train.filters_text = "Ha: H-alpha, HA\nOIII\n\n"
    expect(train.filters).to eq([ { "name" => "Ha", "aliases" => [ "H-alpha", "HA" ] }, { "name" => "OIII", "aliases" => [] } ])
    expect(train.filters_text).to eq("Ha: H-alpha, HA\nOIII")
  end

  it "rejects duplicate filter names" do
    train.filters = [ { "name" => "Ha" }, { "name" => "HA" } ]
    expect(train).not_to be_valid
  end
end
