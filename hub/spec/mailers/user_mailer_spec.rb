require "rails_helper"

RSpec.describe UserMailer, type: :mailer do
  describe "target_progress" do
    let(:user) { create(:user, email: "owner@example.com") }
    let(:target) { create(:target, user: user, name: "M42") }
    let(:event) { create(:target_event, target: target, event_type: :progress) }
    let(:mail) { UserMailer.target_progress(event) }

    it "renders the headers" do
      expect(mail.to).to eq([ "owner@example.com" ])
      expect(mail.from).to eq([ "from@example.com" ])
      expect(mail.subject).to include("M42")
    end

    it "renders the body" do
      expect(mail.body.encoded).to match("M42")
    end
  end
end
