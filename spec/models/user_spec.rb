require "rails_helper"

RSpec.describe User, type: :model do
  it { is_expected.to validate_presence_of(:name) }
  it { is_expected.to have_many(:targets).dependent(:destroy) }

  describe "#admin?" do
    it "is true only for the admin role" do
      expect(build(:user, :admin)).to be_admin
      expect(build(:user)).not_to be_admin
    end
  end

  describe "#sjaa_member?" do
    it "is true when a membership number is present" do
      expect(build(:user, sjaa_membership_number: "12345")).to be_sjaa_member
      expect(build(:user, sjaa_membership_number: nil)).not_to be_sjaa_member
    end
  end
end
