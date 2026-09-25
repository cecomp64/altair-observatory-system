require "rails_helper"

RSpec.describe DataProduct, type: :model do
  it { is_expected.to belong_to(:target) }
  it { is_expected.to validate_presence_of(:url) }
  it do
    is_expected.to define_enum_for(:kind).with_values(
      sub: 0, stacked: 1, preview: 2, log: 3,
      night_master: 4, multi_night_master: 5, project_reference: 6, provisional_noflat: 7
    )
  end

  it "doesn't need a URL for an Altair master (it lives in the archive)" do
    expect(build(:data_product, kind: :night_master, url: nil)).to be_valid
  end

  it "takes its project from the target" do
    product = create(:data_product)
    expect(product.project).to eq(product.target.project)
  end

  it "accepts http(s) URLs" do
    expect(build(:data_product, url: "https://bucket.s3.amazonaws.com/a.fits")).to be_valid
    expect(build(:data_product, url: "HTTP://example.org/a.fits")).to be_valid
  end

  it "rejects URLs that would be unsafe as a link href" do
    [ "javascript:alert(1)", "data:text/html;base64,PHNjcmlwdD4=", "//evil.example/a" ].each do |url|
      expect(build(:data_product, url: url)).not_to be_valid
    end
  end
end
