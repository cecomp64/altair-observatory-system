require "rails_helper"

RSpec.describe ExposurePlan, type: :model do
  it { is_expected.to belong_to(:target) }
  it { is_expected.to validate_presence_of(:filter) }

  describe "#percent_complete" do
    it "computes completed / desired as a percentage" do
      plan = build(:exposure_plan, desired_count: 20, completed_count: 5)
      expect(plan.percent_complete).to eq(25.0)
    end
  end
end
