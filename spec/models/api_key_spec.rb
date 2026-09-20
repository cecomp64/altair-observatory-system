require "rails_helper"

RSpec.describe ApiKey, type: :model do
  describe "#generate_token!" do
    it "stores a digest, not the plaintext token" do
      api_key = build(:api_key, token_digest: nil)
      raw = api_key.generate_token!

      expect(api_key.token_digest).to eq(ApiKey.digest(raw))
      expect(api_key.token_digest).not_to eq(raw)
      expect(api_key.plaintext_token).to eq(raw)
    end
  end

  describe ".authenticate" do
    it "finds the matching active key by raw token" do
      api_key = create(:api_key)
      raw = api_key.plaintext_token

      expect(ApiKey.authenticate(raw)).to eq(api_key)
    end

    it "returns nil for a revoked key" do
      api_key = create(:api_key, active: false)

      expect(ApiKey.authenticate(api_key.plaintext_token)).to be_nil
    end

    it "returns nil for a blank or unknown token" do
      expect(ApiKey.authenticate(nil)).to be_nil
      expect(ApiKey.authenticate("does-not-exist")).to be_nil
    end
  end
end
