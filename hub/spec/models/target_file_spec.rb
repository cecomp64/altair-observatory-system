require "rails_helper"

RSpec.describe TargetFile, type: :model do
  it { is_expected.to belong_to(:target) }
  it { is_expected.to validate_presence_of(:url) }
  it { is_expected.to define_enum_for(:kind).with_values(sub: 0, stacked: 1, preview: 2, log: 3) }

  it "accepts http(s) URLs" do
    expect(build(:target_file, url: "https://bucket.s3.amazonaws.com/a.fits")).to be_valid
    expect(build(:target_file, url: "HTTP://example.org/a.fits")).to be_valid
  end

  it "rejects URLs that would be unsafe as a link href" do
    [ "javascript:alert(1)", "data:text/html;base64,PHNjcmlwdD4=", "//evil.example/a" ].each do |url|
      expect(build(:target_file, url: url)).not_to be_valid
    end
  end
end
