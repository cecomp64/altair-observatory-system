require "rails_helper"

RSpec.describe DataProduct, type: :model do
  it { is_expected.to belong_to(:target) }
  it do
    is_expected.to define_enum_for(:kind).with_values(
      night_master: 4, multi_night_master: 5, project_reference: 6, provisional_noflat: 7
    )
  end

  it "takes its project from the target" do
    product = create(:data_product)
    expect(product.project).to eq(product.target.project)
  end
end
