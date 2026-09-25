FactoryBot.define do
  factory :api_key do
    telescope
    name { "Test worker key" }
    active { true }

    after(:build) do |api_key|
      api_key.generate_token! if api_key.token_digest.blank?
    end
  end
end
