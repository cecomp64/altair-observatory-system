require "rails_helper"

RSpec.describe CoordinateParser do
  describe ".parse_ra" do
    it "accepts decimal degrees" do
      expect(described_class.parse_ra("83.822")).to eq(83.822)
    end

    it "converts sexagesimal hours:minutes:seconds to degrees" do
      # 05:35:17 hours -> (5 + 35/60 + 17/3600) * 15
      expect(described_class.parse_ra("05:35:17")).to be_within(0.01).of(83.821)
    end

    it "rejects out-of-range or garbage input" do
      expect(described_class.parse_ra("400")).to be_nil
      expect(described_class.parse_ra("not a coordinate")).to be_nil
      expect(described_class.parse_ra("")).to be_nil
      expect(described_class.parse_ra(nil)).to be_nil
    end
  end

  describe ".parse_dec" do
    it "accepts decimal degrees, including negative" do
      expect(described_class.parse_dec("-5.391")).to eq(-5.391)
    end

    it "parses sexagesimal declination and preserves sign" do
      expect(described_class.parse_dec("-05:23:28")).to be_within(0.01).of(-5.391)
    end

    it "rejects values outside -90..90" do
      expect(described_class.parse_dec("120")).to be_nil
      expect(described_class.parse_dec("-91")).to be_nil
    end
  end
end
