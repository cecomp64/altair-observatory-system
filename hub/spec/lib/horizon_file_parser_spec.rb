require "rails_helper"

RSpec.describe HorizonFileParser do
  def parse(text) = described_class.parse(text)

  def error_for(text)
    parse(text)
    nil
  rescue described_class::Error => e
    e.message
  end

  it "parses comma or whitespace separated pairs, sorted by azimuth" do
    expect(parse("# comment\n\n180, 20\n0 15\n90\t25.5\n")).to eq([ [ 0.0, 15.0 ], [ 90.0, 25.5 ], [ 180.0, 20.0 ] ])
  end

  it "skips a header row, a byte order mark, CRLF endings and extra columns" do
    expect(parse("\xEF\xBB\xBFazimuth,altitude,note\r\n0,15,tree\r\n360,15\r\n")).to eq([ [ 0.0, 15.0 ], [ 360.0, 15.0 ] ])
  end

  it "accepts altitudes below the horizon" do
    expect(parse("0,-2.5\n")).to eq([ [ 0.0, -2.5 ] ])
  end

  it "rejects a line without an azimuth and an altitude, naming the line" do
    expect(error_for("0,15\n# note\n10\n")).to eq("line 3: expected an azimuth and an altitude")
    expect(error_for("0,15\nten,20\n")).to eq("line 2: expected an azimuth and an altitude")
    expect(error_for("0,high\n")).to eq("line 1: expected an azimuth and an altitude")
  end

  it "only skips a header on the first row" do
    expect(error_for("az,alt\n0,15\naz,alt\n")).to eq("line 3: expected an azimuth and an altitude")
  end

  it "rejects out-of-range values" do
    expect(error_for("0,15\n361,15\n")).to eq("line 2: azimuth must be between 0 and 360")
    expect(error_for("-1,15\n")).to eq("line 1: azimuth must be between 0 and 360")
    expect(error_for("0,91\n")).to eq("line 1: altitude must be between -90 and 90")
    expect(error_for("0,Infinity\n")).to eq("line 1: expected an azimuth and an altitude")
  end

  it "rejects a file with no points" do
    expect(error_for("")).to eq("has no azimuth,altitude pairs")
    expect(error_for("# just a comment\nazimuth,altitude\n")).to eq("has no azimuth,altitude pairs")
    expect(error_for("\x89PNG\x00\x01".b)).to eq("has no azimuth,altitude pairs")
  end

  it "rejects a file over 1 MB" do
    expect(error_for("0,15\n" * 300_000)).to eq("is larger than 1 MB")
  end
end
