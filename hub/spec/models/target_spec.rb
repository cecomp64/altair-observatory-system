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

    it "leaves out paused targets and targets of paused, completed or archived projects" do
      telescope = create(:telescope)
      open = create(:target, telescope: telescope, status: :active)
      paused = create(:target, telescope: telescope, status: :active, paused_at: Time.current)
      halted = %w[paused completed archived].map do |status|
        project = create(:project, status: status)
        create(:target, telescope: telescope, status: :active, project: project, user: project.user)
      end

      expect(Target.schedulable).to contain_exactly(open)
      expect(paused.scheduling_state).to eq("paused")
      expect(halted.map(&:scheduling_state)).to eq(%w[project_paused project_completed project_archived])
      expect(open).to be_schedulable
    end
  end

  describe "pause, resume, reopen and settle" do
    let(:owner) { create(:user) }
    let(:target) { create(:target, user: owner, status: :active) }
    let!(:plan) { create(:exposure_plan, target: target, desired_count: 10, completed_count: 10) }

    it "pauses and resumes an open target, recording who did it" do
      expect(target.pause!(by: owner)).to be(true)
      expect(target.reload).to be_paused
      expect(target.pause!(by: owner)).to be(false)
      expect(target.resume!(by: owner)).to be(true)
      expect(target.reload).not_to be_paused
      expect(target.target_events.status_changed.map { |e| e.payload["status"] }).to eq(%w[paused resumed])
      expect(target.target_events.last.summary).to eq("Resumed by #{owner.display_name}")
    end

    it "completes when everything is captured, and reopens when more frames are wanted" do
      target.settle_status!(by: owner)
      expect(target).to be_completed
      expect(target.reopen!(by: owner)).to be(false)

      plan.update!(desired_count: 20)
      target.settle_status!(by: owner)
      expect(target).to be_active
    end

    it "reopens a cancelled target" do
      target.update!(status: :cancelled)
      plan.update!(completed_count: 2)
      expect(target.reopen!(by: owner)).to be(true)
      expect(target.reload).to be_active
    end
  end
end
