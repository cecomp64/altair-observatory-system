FactoryBot.define do
  factory :api_key do
    transient do
      telescope { nil }
    end

    owner { telescope || association(:telescope) }
    name { "Test worker key" }
    active { true }

    after(:build) do |api_key|
      api_key.generate_token! if api_key.token_digest.blank?
    end

    factory :node_api_key do
      transient do
        processing_node { nil }
      end

      owner { processing_node || association(:processing_node) }
      name { "Test node key" }
    end
  end
end
