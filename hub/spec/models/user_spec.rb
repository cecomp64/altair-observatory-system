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

  describe "SJAA linking" do
    def sjaa_person(id: 42, email: "ada@example.com", ends: [ Date.current + 30 ], lifetime: false)
      Sjaa::Person.new(id: id, first_name: "Ada", last_name: "Lovelace", email: email, emails: [ email ],
                       membership_ends: ends, lifetime: lifetime)
    end

    it "creates a linked account that mirrors the SJAA person" do
      user = described_class.find_or_create_from_sjaa!(sjaa_person)
      expect(user).to be_persisted
      expect(user).to have_attributes(name: "Ada Lovelace", email: "ada@example.com", sjaa_person_id: 42,
                                      sjaa_membership_expires_on: Date.current + 30)
      expect(user).to be_sjaa_linked
      expect(user).to be_sjaa_membership_current
    end

    it "links the account with the same email, or the one already linked, updating name and email" do
      by_email = create(:user, email: "ada@example.com", name: "A. L.")
      expect(described_class.find_or_create_from_sjaa!(sjaa_person)).to eq(by_email)
      expect(by_email.reload.name).to eq("Ada Lovelace")

      expect(described_class.find_or_create_from_sjaa!(sjaa_person(email: "ada@new.example.com"))).to eq(by_email)
      expect(by_email.reload.email).to eq("ada@new.example.com")
    end

    it "keeps its email when another account already uses SJAA's" do
      create(:user, email: "taken@example.com")
      user = create(:user, email: "mine@example.com")
      user.link_sjaa!(sjaa_person(email: "taken@example.com"))
      expect(user.reload.email).to eq("mine@example.com")
    end

    it "is current until the membership ends, always for lifetime members, and never unlinked" do
      user = create(:user)
      user.link_sjaa!(sjaa_person(ends: [ Date.current ]))
      expect(user.sjaa_membership_current?).to be true
      expect(user.sjaa_membership_current?(Date.current + 1)).to be false

      user.link_sjaa!(sjaa_person(ends: [], lifetime: true))
      expect(user.sjaa_membership_current?(Date.current + 10.years)).to be true

      user.link_sjaa!(sjaa_person(ends: [ Date.current - 1 ]))
      expect(user.sjaa_membership_current?).to be false

      user.link_sjaa!(sjaa_person)
      user.unlink_sjaa!
      expect(user).not_to be_sjaa_linked
      expect(user.sjaa_membership_current?).to be false
    end
  end
end
