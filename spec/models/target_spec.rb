require "rails_helper"

RSpec.describe Target, type: :model do
  describe "validations" do
    it { is_expected.to validate_presence_of(:name) }
    it { is_expected.to belong_to(:user) }
    it { is_expected.to belong_to(:telescope) }
    it { is_expected.to have_many(:exposure_plans).dependent(:destroy) }
  end

  describe "#percent_complete" do
    it "returns 0 when there are no exposure plans" do
      target = build_stubbed(:target)
      allow(target).to receive(:exposure_plans).and_return(ExposurePlan.none)
      expect(target.percent_complete).to eq(0)
    end

    it "computes the weighted percentage across all exposure plans" do
      target = create(:target)
      create(:exposure_plan, target: target, desired_count: 10, completed_count: 5)
      create(:exposure_plan, target: target, desired_count: 10, completed_count: 5)

      expect(target.percent_complete).to eq(50.0)
    end
  end

  describe "#fully_captured?" do
    it "is false when any exposure plan is incomplete" do
      target = create(:target)
      create(:exposure_plan, target: target, desired_count: 10, completed_count: 10)
      create(:exposure_plan, target: target, desired_count: 10, completed_count: 9)

      expect(target.fully_captured?).to be(false)
    end

    it "is true when every exposure plan has met its desired count" do
      target = create(:target)
      create(:exposure_plan, target: target, desired_count: 10, completed_count: 10)
      create(:exposure_plan, target: target, desired_count: 5, completed_count: 5)

      expect(target.fully_captured?).to be(true)
    end
  end

  describe "#submit!" do
    it "moves a draft target to submitted and stamps submitted_at" do
      target = create(:target, status: :draft, submitted_at: nil)

      target.submit!

      expect(target).to be_submitted
      expect(target.submitted_at).to be_present
    end
  end

  describe ".schedulable" do
    it "includes submitted, active, and in_progress targets but not draft/completed/cancelled" do
      telescope = create(:telescope)
      schedulable = %i[submitted active in_progress].map { |status| create(:target, telescope: telescope, status: status) }
      not_schedulable = %i[draft completed cancelled].map { |status| create(:target, telescope: telescope, status: status) }

      expect(Target.schedulable).to include(*schedulable)
      expect(Target.schedulable).not_to include(*not_schedulable)
    end
  end
end
