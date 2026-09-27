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

RSpec.describe "CoordinateParser with the Hub's display formats" do
  it "reads back what CoordinateFormatter prints" do
    [ [ 83.822, -5.391 ], [ 10.68479, 41.26906 ], [ 201.365, -43.019 ], [ 0.0, 0.0 ] ].each do |ra, dec|
      expect(CoordinateParser.parse_ra(CoordinateFormatter.ra(ra))).to be_within(0.001).of(ra)
      expect(CoordinateParser.parse_dec(CoordinateFormatter.dec(dec))).to be_within(0.001).of(dec)
    end
    expect(CoordinateParser.parse_latitude(CoordinateFormatter.latitude(-33.87))).to be_within(0.001).of(-33.87)
    expect(CoordinateParser.parse_longitude(CoordinateFormatter.longitude(-121.9))).to be_within(0.001).of(-121.9)
  end

  it "accepts h/m/s and d/m/s markers, unicode minus, and hours as a decimal" do
    expect(CoordinateParser.parse_ra("05h35m17s")).to be_within(0.001).of(83.8208)
    expect(CoordinateParser.parse_ra("5.5881h")).to be_within(0.001).of(83.8215)
    expect(CoordinateParser.parse_dec("−05° 23′ 28″")).to be_within(0.001).of(-5.3911)
    expect(CoordinateParser.parse_dec("-5d23m28s")).to be_within(0.001).of(-5.3911)
  end

  it "reads hemispheres and rejects a sign with one" do
    expect(CoordinateParser.parse_latitude("37 18 00 N")).to eq(37.3)
    expect(CoordinateParser.parse_longitude("121° 54′ W")).to be_within(0.0001).of(-121.9)
    expect(CoordinateParser.parse_latitude("-37 S")).to be_nil
    expect(CoordinateParser.parse_latitude("91")).to be_nil
    expect(CoordinateParser.parse_longitude("181 E")).to be_nil
  end
end

RSpec.describe CoordinateFormatter do
  it "prints RA in hours and Dec, latitude and longitude in degrees, minutes, seconds" do
    expect(described_class.ra(83.822)).to eq("05h 35m 17.3s")
    expect(described_class.dec(-5.391)).to eq("−05° 23′ 28″")
    expect(described_class.dec(41.26906)).to eq("+41° 16′ 09″")
    expect(described_class.latitude(37.3)).to eq("37° 18′ 00″ N")
    expect(described_class.longitude(-121.9)).to eq("121° 54′ 00″ W")
  end

  it "carries rounding into the next unit and wraps RA at 24h" do
    expect(described_class.ra(359.99999)).to eq("00h 00m 00.0s")
    expect(described_class.dec(10.99999)).to eq("+11° 00′ 00″")
  end

  it "prints altitudes and other angles as decimal degrees" do
    expect(described_class.angle(62.4)).to eq("62°")
    expect(described_class.angle(-3.2)).to eq("−3°")
    expect(described_class.angle(1.25, digits: 1)).to eq("1.3°")
  end
end
