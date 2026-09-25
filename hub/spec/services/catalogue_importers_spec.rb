require "rails_helper"

RSpec.describe "Catalogue importers" do
  def fixture(name) = Rails.root.join("spec/fixtures/catalogue", name).read

  describe Catalogue::OpenNgcImporter do
    it "imports NGC/IC objects with Messier numbers and common names as aliases" do
      result = described_class.import(fixture("openngc_sample.csv"))

      expect(result.to_h).to include("imported" => 5, "skipped" => 2, "errors" => 0)
      m31 = AstroObject.find_by_alias("M31")
      expect(m31).to have_attributes(primary_name: "Andromeda Galaxy", source: "openngc", constellation: "And", object_type: "Galaxy")
      expect(m31.ra_deg.to_f).to be_within(0.0001).of(10.68479)
      expect(m31.alias_names).to include("NGC 224", "M 31", "Andromeda Galaxy")
      expect(AstroObject.find_by_alias("Orion Nebula")).to eq(AstroObject.find_by_alias("M 42"))
      expect(AstroObject.find_by_alias("NGC 2")).to be_nil # a duplicate entry, skipped
    end

    it "is idempotent" do
      described_class.import(fixture("openngc_sample.csv"))
      expect { described_class.import(fixture("openngc_sample.csv")) }.not_to change { [ AstroObject.count, ObjectAlias.count ] }
    end

    it "merges into an object that already exists under another name, filling only gaps" do
      existing = create(:astro_object, primary_name: "M 31", source: "telescopius", constellation: nil, magnitude: 3.4)
      described_class.import(fixture("openngc_sample.csv"))

      expect(AstroObject.find_by_alias("NGC 224")).to eq(existing)
      expect(existing.reload).to have_attributes(constellation: "And", magnitude: 3.4, source: "telescopius")
    end
  end

  it "imports LDN and LBN from VizieR CSV, skipping incomplete rows" do
    ldn = Catalogue::LdnImporter.import(fixture("ldn_sample.csv"))
    lbn = Catalogue::LbnImporter.import(fixture("lbn_sample.csv"))

    expect(ldn.to_h).to include("imported" => 2, "skipped" => 1)
    expect(lbn.to_h).to include("imported" => 1)
    expect(AstroObject.find_by_alias("LDN1235")).to have_attributes(object_type: "Dark Nebula", source: "ldn")
    expect(AstroObject.find_by_alias("LBN 331").dec_deg.to_f).to eq(44.0)
  end
end
