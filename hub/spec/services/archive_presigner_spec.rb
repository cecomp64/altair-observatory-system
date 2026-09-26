require "rails_helper"

RSpec.describe Archive::Presigner do
  let(:presigner) do
    described_class.new(access_key_id: "AKIA_TEST", secret_access_key: "secret", region: "us-west-2", bucket: "astro-archive")
  end

  def product(uri) = build(:data_product, archive_uri: uri)

  it "signs masters under the archive's projects/ and calibration/masters/ trees" do
    url = presigner.url_for(product("s3://astro-archive/altair/projects/T34_m31/multinight/Ha_v7.xisf"))

    uri = URI(url)
    expect(uri.host).to include("astro-archive")
    expect(uri.path).to eq("/altair/projects/T34_m31/multinight/Ha_v7.xisf")
    params = Rack::Utils.parse_query(uri.query)
    expect(params["X-Amz-Expires"]).to eq("600")
    expect(params["response-content-disposition"]).to include("Ha_v7.xisf")
    expect(presigner.downloadable?(product("s3://astro-archive/altair/calibration/masters/flat_Ha.xisf"))).to be(true)
  end

  it "never signs raw frames, other buckets or path tricks" do
    [ "s3://astro-archive/altair/raw/esprit/2026-09-24/a.fits",
      "s3://other-bucket/altair/projects/T34/multinight/Ha.xisf",
      "s3://astro-archive/altair/projects/../raw/a.fits",
      "/nas/astro/projects/T34/multinight/Ha.xisf", nil ].each do |uri|
      expect(presigner.downloadable?(product(uri))).to be(false), uri.inspect
      expect { presigner.url_for(product(uri)) }.to raise_error(described_class::NotDownloadable)
    end
  end

  it "is disabled without credentials" do
    unconfigured = described_class.new(access_key_id: nil, secret_access_key: nil, region: nil, bucket: nil)
    expect(unconfigured.enabled?).to be(false)
    expect(unconfigured.downloadable?(product("s3://astro-archive/altair/projects/x/multinight/a.xisf"))).to be(false)
  end
end
