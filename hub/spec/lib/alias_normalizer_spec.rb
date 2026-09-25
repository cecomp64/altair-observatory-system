require "rails_helper"

RSpec.describe Catalogue::AliasNormalizer do
  it "normalises spacing, case, punctuation and zero padding" do
    expect(described_class.normalize("M 31")).to eq("m31")
    expect(described_class.normalize("NGC0224")).to eq("ngc224")
    expect(described_class.normalize("Sh2-155")).to eq("sh2155")
    expect(described_class.normalize("  ")).to be_nil
  end

  it "detects the catalogue of a designation" do
    expect(described_class.catalog_for("M 31")).to eq("Messier")
    expect(described_class.catalog_for("NGC 7000")).to eq("NGC")
    expect(described_class.catalog_for("LDN 1235")).to eq("LDN")
    expect(described_class.catalog_for("Pelican Nebula")).to be_nil
  end
end
